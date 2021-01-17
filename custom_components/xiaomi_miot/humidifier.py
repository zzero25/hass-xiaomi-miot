"""Support for humidifier and dehumidifier."""
import logging

from enum import Enum
from homeassistant.const import *
from homeassistant.components.humidifier import (
    HumidifierEntity,
)
from homeassistant.components.humidifier.const import *

from . import (
    DOMAIN,
    CONF_MODEL,
    MiotDevice,
    MiotEntity,
    bind_services_to_entries,
)

_LOGGER = logging.getLogger(__name__)
DATA_KEY = f'humidifier.{DOMAIN}'

SERVICE_TO_METHOD = {}


async def async_setup_entry(hass, config_entry, async_add_entities):
    config = hass.data[DOMAIN]['configs'].get(config_entry.entry_id, dict(config_entry.data))
    await async_setup_platform(hass, config, async_add_entities)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    hass.data.setdefault(DATA_KEY, {})
    config.setdefault('add_entities', {})
    config['add_entities']['humidifier'] = async_add_entities
    model = str(config.get(CONF_MODEL) or '')
    entities = []
    if model.find('derh') >= 0:
        entity = MiotDehumidifierEntity(config)
        entities.append(entity)
    for entity in entities:
        hass.data[DOMAIN]['entities'][entity.unique_id] = entity
    async_add_entities(entities, update_before_add=True)
    bind_services_to_entries(hass, SERVICE_TO_METHOD)


class MiotDehumidifierModes(Enum):
    Off = -1
    TargetHumid = 0
    DryCloth = 1


class MiotDehumidifierEntity(MiotEntity, HumidifierEntity):
    mapping = {
        # http://miot-spec.org/miot-spec-v2/instance?type=urn:miot-spec-v2:device:dehumidifier:0000A02D:nwt-312en:1
        "power":             {"siid": 2, "piid": 1},  # bool
        "fault":             {"siid": 2, "piid": 2},  # [0]
        "mode":              {"siid": 2, "piid": 3},  # 0 - Target-humid, 1 - Dry-cloth
        "target_humidity":   {"siid": 2, "piid": 5},  # [30, 40, 50, 60, 70]
        "fan_level":         {"siid": 2, "piid": 7},  # 0 - Auto, 1 - Level1
        "relative_humidity": {"siid": 3, "piid": 1},  # [0, 100], step 1
        "temperature":       {"siid": 3, "piid": 7},  # [-30, 100], step 1
        "alarm":             {"siid": 4, "piid": 1},  # bool
        "indicator_light":   {"siid": 5, "piid": 1},  # bool
        "physical_controls_locked": {"siid": 6, "piid": 1},  # bool
        "coil_temp":         {"siid": 7, "piid": 1},  # [-30, 200], step 1
        "compressor_status": {"siid": 7, "piid": 2},  # bool
        "water_tank_status": {"siid": 7, "piid": 3},  # bool
        "defrost_status":    {"siid": 7, "piid": 4},  # bool
        "timer_service":     {"siid": 8, "piid": 1},  # [0, 65535], step 1
        "timer_setting":     {"siid": 8, "piid": 2},  # [0, 1, 2, 4, 8, 12]
    }

    def __init__(self, config):
        name = config[CONF_NAME]
        host = config[CONF_HOST]
        token = config[CONF_TOKEN]
        model = config.get(CONF_MODEL)
        _LOGGER.info('Initializing with host %s (token %s...)', host, token[:5])

        self._device = MiotDevice(host, token)
        super().__init__(name, self._device)

        self._supported_features = SUPPORT_MODES
        self._state_attrs.update({'entity_class': self.__class__.__name__})

    async def async_update(self):
        await super().async_update()
        if self._available:
            self._state_attrs.update({
                ATTR_HUMIDITY: self.target_humidity,
                ATTR_MODE:     self.mode,
            })

    @property
    def device_class(self):
        return DEVICE_CLASS_DEHUMIDIFIER

    @property
    def state_attributes(self):
        return self._state_attrs

    def turn_on(self, **kwargs):
        return self.set_property('power', True)

    def turn_off(self, **kwargs):
        return self.set_property('power', False)

    @property
    def target_humidity(self):
        return int(self._state_attrs.get('target_humidity', 0))

    def set_humidity(self, humidity: int):
        num = 70
        for n in [30, 40, 50, 60, 70]:
            if humidity < n:
                num = n
                break
        ret = self._device.set_property('target_humidity', num)
        if ret:
            self._state_attrs.update({
                'target_humidity': num,
            })
        return ret

    @property
    def mode(self):
        mode = -1
        if self._state:
            mode = int(self._state_attrs.get('mode', -1))
        raise MiotDehumidifierModes(mode).name

    @property
    def available_modes(self):
        raise [v.name for v in MiotDehumidifierModes]

    def set_mode(self, mode: str):
        mod = MiotDehumidifierModes[mode]
        if mod == -1 or mode == MiotDehumidifierModes(-1).name:
            ret = self._device.set_property('power', False)
        else:
            ret = self._device.set_property('mode', mod)
            if ret:
                self._state_attrs.update({
                    'mode': mod,
                })
        return ret

    @property
    def min_humidity(self):
        return 30

    @property
    def max_humidity(self):
        return 70
