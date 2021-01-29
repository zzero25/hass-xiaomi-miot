"""Support for Xiaomi switches."""
import logging

from homeassistant.const import *
from homeassistant.components.switch import (
    DOMAIN as ENTITY_DOMAIN,
    SwitchEntity,
    DEVICE_CLASS_SWITCH,
    DEVICE_CLASS_OUTLET,
)

from . import (
    DOMAIN,
    CONF_MODEL,
    XIAOMI_CONFIG_SCHEMA as PLATFORM_SCHEMA,  # noqa: F401
    MiioDevice,
    MiotDevice,
    MiioEntity,
    MiotToggleEntity,
    ToggleSubEntity,
    bind_services_to_entries,
)
from .core.miot_spec import (
    MiotSpec,
    MiotService,
)

_LOGGER = logging.getLogger(__name__)
DATA_KEY = f'{ENTITY_DOMAIN}.{DOMAIN}'

SERVICE_TO_METHOD = {}


async def async_setup_entry(hass, config_entry, async_add_entities):
    config = hass.data[DOMAIN]['configs'].get(config_entry.entry_id, dict(config_entry.data))
    await async_setup_platform(hass, config, async_add_entities)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    hass.data.setdefault(DATA_KEY, {})
    config.setdefault('add_entities', {})
    config['add_entities'][ENTITY_DOMAIN] = async_add_entities
    model = str(config.get(CONF_MODEL) or '')
    entities = []
    if model in ['pwzn.relay.banana']:
        entities.append(PwznRelaySwitchEntity(config))
    else:
        miot = config.get('miot_type')
        if miot:
            spec = await MiotSpec.async_from_type(hass, miot)
            for srv in spec.get_services(ENTITY_DOMAIN, 'outlet', 'relay'):
                if not srv.get_property('on'):
                    continue
                cfg = {
                    **config,
                    'name': f"{config.get('name')} {srv.description}"
                }
                entities.append(MiotSwitchEntity(cfg, srv))
    for entity in entities:
        hass.data[DOMAIN]['entities'][entity.unique_id] = entity
    async_add_entities(entities, update_before_add=True)
    bind_services_to_entries(hass, SERVICE_TO_METHOD)


class MiotSwitchEntity(MiotToggleEntity, SwitchEntity):
    def __init__(self, config: dict, miot_service: MiotService):
        name = config[CONF_NAME]
        host = config[CONF_HOST]
        token = config[CONF_TOKEN]
        _LOGGER.info('Initializing %s with host %s (token %s...)', name, host, token[:5])

        mapping = miot_service.spec.services_mapping(ENTITY_DOMAIN, 'indicator_light', 'switch_control') or {}
        mapping.update(miot_service.mapping())
        self._device = MiotDevice(mapping, host, token)
        super().__init__(name, self._device, miot_service)
        self._state_attrs.update({'entity_class': self.__class__.__name__})

    @property
    def device_class(self):
        typ = f'{self._model} {self._miot_service.spec.type}'
        if typ.find('outlet') >= 0:
            return DEVICE_CLASS_OUTLET
        return DEVICE_CLASS_SWITCH


class PwznRelaySwitchEntity(MiioEntity, SwitchEntity):
    def __init__(self, config: dict):
        name = config[CONF_NAME]
        host = config[CONF_HOST]
        token = config[CONF_TOKEN]
        _LOGGER.info('Initializing %s with host %s (token %s...)', name, host, token[:5])

        self._config = config
        self._device = MiioDevice(host, token)
        super().__init__(name, self._device)
        self._add_entities = config.get('add_entities') or {}
        self._state_attrs.update({'entity_class': self.__class__.__name__})
        self._success_result = [0]

        self._props = [
            'relay_names_g1', 'relay_status_g1',
            'relay_names_g2', 'relay_status_g2',
            'g2Enable', 'codeEnable',
        ]
        self._subs = {}

    @property
    def device_class(self):
        return DEVICE_CLASS_SWITCH

    async def async_update(self):
        await super().async_update()
        await self.hass.async_add_executor_job(self.update_all)

    def update_all(self):
        if self._available:
            attrs = self._state_attrs
            self._state = False
            add_switches = self._add_entities.get(ENTITY_DOMAIN)
            idx = 0
            for g in [1, 2]:
                if f'relay_status_g{g}' not in attrs:
                    continue
                sta = int(attrs.get(f'relay_status_g{g}') or 0)
                if sta:
                    self._state = True
                nms = str(attrs.get(f'relay_names_g{g}') or '').split('-')
                s = 0
                b = 1
                for n in nms:
                    s += 1
                    k = f'g{g}s{s}'
                    self._state_attrs[k] = STATE_ON if sta & b else STATE_OFF
                    if k in self._subs:
                        self._subs[k].update()
                    elif add_switches:
                        self._subs[k] = PwznRelaySwitchSubEntity(self, g, s, {
                            'attr': k,
                            'index': idx,
                        })
                        add_switches([self._subs[k]])
                    b <<= 1
                    idx += 1

            if 'advanced' in self._config.get(CONF_MODE):
                for k in ['g2Enable', 'codeEnable']:
                    if k not in attrs:
                        continue
                    self._state_attrs[k] = STATE_ON if attrs[k] else STATE_OFF
                    if k in self._subs:
                        self._subs[k].update()
                    elif add_switches:
                        self._subs[k] = PwznRelaySwitchSubEntity(self, 0, 0, {
                            'attr': k,
                        })
                        add_switches([self._subs[k]])

    def turn_on(self, **kwargs):
        ret = self.send_command('power_all', [1])
        if ret:
            full = (1 << 16) - 1
            self.update_attrs({
                'relay_status_g1': full,
                'relay_status_g2': full,
            }, update_parent=False)
            self.update_all()
            self._state = True
        return ret

    def turn_off(self, **kwargs):
        ret = self.send_command('power_all', [0])
        if ret:
            self.update_attrs({
                'relay_status_g1': 0,
                'relay_status_g2': 0,
            }, update_parent=False)
            self.update_all()
            self._state = False
        return ret


class SwitchSubEntity(ToggleSubEntity, SwitchEntity):
    def update(self):
        super().update()


class PwznRelaySwitchSubEntity(SwitchSubEntity):
    def __init__(self, parent: PwznRelaySwitchEntity, group, switch, option=None):
        self._group = group
        self._switch = switch
        self._switch_index = 0
        key = f'g{group}s{switch}'
        if isinstance(option, dict):
            if option.get('attr'):
                key = option.get('attr')
            self._switch_index = int(option.get('index') or 0)
        super().__init__(parent, key, option)

    def turn_parent(self, on):
        if self._attr == 'g2Enable':
            ret = self.call_parent('send_command', 'set_g2enable', [1 if on else 0])
        elif self._attr == 'codeEnable':
            ret = self.call_parent('send_command', 'set_codeEnable', [1 if on else 0])
        else:
            ret = self.call_parent('send_command', 'power_on' if on else 'power_off', [self._switch_index])
        if ret:
            self.update_attrs({
                self._attr: STATE_ON if on else STATE_OFF
            }, update_parent=True)
            self._state = on and True
        return ret

    def turn_on(self, **kwargs):
        return self.turn_parent(True)

    def turn_off(self, **kwargs):
        return self.turn_parent(False)
