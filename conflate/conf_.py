import importlib
import logging
import weakref
from contextlib import contextmanager
from copy import deepcopy

from conflate.error import (ConfGroupExistsError, FrozenConfGroupError,
                            FrozenConfPropError, UnknownConfError)

logger = logging.getLogger(__name__)


def _get_obj_attr(obj, attr):
    return object.__getattribute__(obj, attr)


class Conf:
    '''Configuration Manager

    >>> conf = Conf()
    >>> conf.add_group('dummy', opt1=3, opt2='some string')
    >>> conf.dummy.opt1
    3
    >>> conf.dummy.opt2 = 'other string'
    Traceback (most recent call last):
        ...
    conflate.error.FrozenConfPropError: Configuration properties are frozen.
    Configuration properties can only be changed globally by loading configuration file through ``Conf.load_conf_file()`` and ``Conf.load_conf_module()``.
    And it can be changed locally in the context created by `Conf.local_env()`.

    '''  # noqa
    __slots__ = ('_is_setting_imported',
                 '_is_frozen',
                 '_conf_depot',
                 '_conf_groups',
                 '__weakref__',
                 )

    def __init__(self):
        from conflate.conf_depot import ConfDepot
        self._is_setting_imported = False
        self._is_frozen = True
        self._conf_depot = ConfDepot()
        self._conf_groups = {}

    def add_group(self, name, **default_properties):
        '''Add new configuration group and all property names with default values

        >>> conf = Conf()

        Add new group and properties through context manager

        >>> with conf.add_group('yummy') as yummy:
        ...     yummy.kind='seafood'
        ...     yummy.name='fish'
        >>> conf.yummy.name
        'fish'

        Add new group and properties through function call

        >>> conf.add_group('dummy', num_prop=3, str_prop='some string')
        >>> conf.dummy.num_prop
        3
        '''
        if name in self._conf_groups:
            raise ConfGroupExistsError(
                f'configuration group {name!r} already exists')

        with self._mutable_conf_ctx():
            group = ConfGroup(weakref.proxy(self), name, default_properties)
            self._conf_groups[name] = group
            if not default_properties:
                return group._register_ctx()

    def _backup(self):
        return deepcopy(self._conf_groups)

    def _restore(self, conf_groups):
        self._conf_groups = conf_groups

    @contextmanager
    def local_env(self):
        '''Return a context manager that makes this Conf mutable temporarily.
        All configuration properties will be restored upon completion of the block.

        >>> conf = Conf()
        >>> conf.add_group('dummy', opt1=3, opt2='some string')
        >>> with conf.local_env():
        ...     conf.dummy.opt1 = 5
        ...     conf.dummy.opt1
        5
        >>> conf.dummy.opt1
        3

        '''  # noqa
        conf_groups_backup = self._backup()
        with self._mutable_conf_ctx():
            yield
        self._restore(conf_groups_backup)

    @contextmanager
    def _mutable_conf_ctx(self):
        self._is_frozen = False
        yield
        self._is_frozen = True

    @contextmanager
    def _conflate_c_ctx(self):
        import conflate
        conflate.c = self._conf_depot
        yield
        del conflate.c

    def __contains__(self, group_name):
        return group_name in self._conf_groups

    def __getitem__(self, group_name):
        return getattr(self, group_name)

    def __getattr__(self, group_name):
        conf_groups = _get_obj_attr(self, '_conf_groups')
        conf_depot = _get_obj_attr('_conf_depot')
        if group_name not in conf_groups:
            raise UnknownConfError(
                f'Unknown configuration group {group_name!r}')

        conf_group = conf_groups[group_name]

        if group_name in conf_depot:
            conf_depot_group = conf_depot[group_name]

            for conf_property, value in conf_depot_group.items():
                conf_group[conf_property] = value

        return conf_group

    def __setattr__(self, name, value):
        if name in self.__slots__:
            object.__setattr__(self, name, value)
        else:
            raise FrozenConfGroupError(
                'Configuration groups are frozen. '
                'Call `conflate.add_group()` for '
                'registering new configuration group.'
            )

    def __dir__(self):
        return self._conf_groups.__dir__()

    def __deepcopy__(self, memo):
        cls = type(self)
        new_self = cls.__new__(cls)
        new_self._is_setting_imported = self._is_setting_imported
        new_self._is_frozen = self._is_frozen
        new_self._conf_depot = deepcopy(self._conf_depot)
        new_self._conf_groups = deepcopy(self._conf_groups)

        for group in new_self._conf_groups.values():
            group._conf = weakref.proxy(new_self)

        return new_self

    def load_conf_file(self, path):
        '''Load python configuration file through file path.

        All configuration groups and properties should be registered first.

        >>> conf = Conf()
        >>> conf.load_conf_file('path/to/conf.py')  # doctest: +SKIP

        Configuration file example
        ```
        from conflate import c
        c.yammy.kind = 'seafood'
        c.yammy.name = 'fish'
        ```
        '''
        from pathlib import Path
        if not isinstance(path, Path):
            path = Path(path)

        with self._mutable_conf_ctx():
            with self._conflate_c_ctx():
                exec(path.open('r').read())

    def load_conf_module(self, module_name):
        '''Load python configuration file through import.
        The module should be importable either through PYTHONPATH
        or was install as a package.

        All configuration groups and properties should be registered first.

        >>> conf = Conf()
        >>> conf.load_conf_file('path/to/conf.py')  # doctest: +SKIP

        Configuration file example
        ```
        from conflate import c
        c.yammy.kind = 'seafood'
        c.yammy.name = 'fish'
        ```
        '''  # noqa
        with self._mutable_conf_ctx():
            with self._conflate_c_ctx():
                importlib.import_module(module_name)

    def set_conf_file(self, path):
        '''

        '''

    def set_conf_module(self, module_name):
        pass


class ConfGroup:
    __slots__ = '_conf', '_name', '_properties', '_is_registering'

    def __init__(self, conf: Conf, name: str, default_properties: dict):
        self._conf = conf
        self._name = name
        self._is_registering = False
        self._properties = default_properties

    def __getattr__(self, key):
        if key not in _get_obj_attr(self, '_properties'):
            raise UnknownConfError(
                f'Unknown {key!r} property in '
                f'configuration group {self._name!r}')

        return object.__getattribute__(self, '_properties')[key]

    def __deepcopy__(self, memo):
        cls = type(self)
        new_self = cls.__new__(cls)
        new_self._conf = self._conf  # Don't need to copy conf
        new_self._name = self._name
        new_self._is_registering = self._is_registering
        new_self._properties = deepcopy(self._properties)
        return new_self

    def __setattr__(self, key, value):
        if key in self.__slots__:
            object.__setattr__(self, key, value)
        elif self._is_registering:
            self._properties[key] = value
        elif key not in self._properties:
            raise UnknownConfError(
                f'Unknown {key!r} property in '
                'configuration group {self.name!r}')
        elif self._conf._is_frozen:
            raise FrozenConfPropError(
                'Configuration properties are frozen.\n'
                'Configuration properties can only be changed globally by '
                'loading configuration file through '
                '``Conf.load_conf_file()`` and ``Conf.load_conf_module()``.\n'
                'And it can be changed locally in the context '
                'created by `Conf.local_env()`.'
            )
        else:
            self._properties[key] = value

    def __dir__(self):
        return self._properties.__dir__()

    @contextmanager
    def _register_ctx(self):
        self._is_registering = True
        yield self
        self._is_registering = False
