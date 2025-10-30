# Copyright(C) 2010-2023 Romain Bignon
#
# This file is part of woob.
#
# woob is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# woob is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with woob. If not, see <http://www.gnu.org/licenses/>.

from __future__ import annotations

import importlib
import importlib.util
import logging
import pkgutil
import sys
import warnings
from collections.abc import Iterator, Mapping
from importlib import metadata
from importlib.machinery import ModuleSpec
from inspect import getmodule
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, cast

from packaging.version import Version

from woob import __version__
from woob.capabilities.base import Capability
from woob.exceptions import ModuleLoadError
from woob.tools.backend import BackendConfig, Module
from woob.tools.log import getLogger
from woob.tools.packaging import parse_requirements
from woob.tools.storage import IStorage


if TYPE_CHECKING:
    from woob.core import Woob
    from woob.core.repositories import Repositories

__all__ = ["LoadedModule", "ModulesLoader", "RepositoryModulesLoader"]


class LoadedModule:
    klass: type[Module]

    def __init__(self, package: ModuleType) -> None:
        self.logger = getLogger("woob.backend")
        self.package = package
        klass: type[Module] | None = None

        full_name = package.__name__
        for attrname in dir(self.package):
            attr: type[Module] = getattr(self.package, attrname)

            # Check that the attribute is indeed a 'Module' subclass.
            # Note that we check below if it is indeed defined in the
            # Python module we're importing, so 'Module' itself or
            # 'Module' subclasses imported from other Python modules
            # won't be taken into account.
            try:
                if not issubclass(attr, Module):
                    continue
            except TypeError:
                # Argument 1 must be a class.
                continue

            # Check that the attribute is indeed defined in the loaded
            # Python module specifically.
            module = getmodule(attr)
            if module is None:
                continue

            module_name = module.__name__
            if not module_name.startswith(full_name) or module_name[len(full_name) :][:1] not in ("", "."):
                continue

            # Check that there is indeed only one Module subclass defined
            # in the Python module.

            if klass is not None:
                raise ImportError(
                    f'At least two modules are defined in "{full_name}": ' + f"{attr!r} and {klass!r}",
                )

            klass = attr

        if not klass:
            raise ImportError(
                f"{package} is not a backend (no Module class found)",
            )

        self.klass = klass

    @property
    def name(self) -> str:
        return self.klass.NAME

    @property
    def maintainer(self) -> str:
        return f"{self.klass.MAINTAINER} <{self.klass.EMAIL}>"

    @property
    def version(self) -> str:
        warnings.warn("The LoadedModule.version attribute will be removed.", DeprecationWarning, stacklevel=2)

        return Version(__version__).base_version

    @property
    def description(self) -> str:
        return self.klass.DESCRIPTION

    @property
    def license(self) -> str:
        return self.klass.LICENSE

    @property
    def config(self) -> BackendConfig:
        return self.klass.CONFIG

    @property
    def website(self) -> str | None:
        if self.klass.BROWSER and hasattr(self.klass.BROWSER, "BASEURL") and self.klass.BROWSER.BASEURL:
            return cast(str, self.klass.BROWSER.BASEURL)
        if self.klass.BROWSER and hasattr(self.klass.BROWSER, "DOMAIN") and self.klass.BROWSER.DOMAIN:
            return f"{self.klass.BROWSER.PROTOCOL}://{self.klass.BROWSER.DOMAIN}"  # type: ignore[attr-defined]
        return None

    @property
    def icon(self) -> str | None:
        return self.klass.ICON

    @property
    def path(self) -> str | None:
        assert self.package is not None
        try:
            return self.package.__path__[0]
        except AttributeError:
            # This might yield 'mymodule/__init__.py' instead of 'mymodule'
            # like the previous version, so we keep the first version if avail.
            mod = getmodule(self.package)
            return mod.__file__ if mod else None

    @property
    def dependencies(self) -> tuple[str, ...]:
        return self.klass.DEPENDENCIES

    def iter_caps(self) -> Iterator[type[Capability]]:
        yield from self.klass.iter_caps()

    def has_caps(self, *caps: str | type[Capability]) -> bool:
        """Return True if module implements at least one of the caps."""
        available_cap_names = [cap.__name__ for cap in self.iter_caps()]
        return any(
            (isinstance(c, str) and c in available_cap_names) or (isinstance(c, type) and issubclass(self.klass, c))
            for c in caps
        )

    def create_instance(
        self,
        woob: Woob,
        backend_name: str,
        config: Mapping[str, Any] | None,
        storage: IStorage | None,
        nofail: bool = False,
        logger: logging.Logger | None = None,
    ) -> Module:
        backend_instance = self.klass(woob, backend_name, config, storage, logger=logger or self.logger, nofail=nofail)
        self.logger.debug('Created backend "%s" for module "%s"', backend_name, self.name)
        return backend_instance


def _add_in_modules_path(path: str) -> None:
    try:
        import woob_modules
    except ImportError:
        from types import ModuleType

        woob_modules = ModuleType("woob_modules")
        sys.modules["woob_modules"] = woob_modules

        woob_modules.__path__ = [path]
    else:
        if path not in woob_modules.__path__:
            woob_modules.__path__.append(path)


class ModulesLoader:
    """
    Load modules.
    """

    LOADED_MODULE = LoadedModule

    def __init__(self, path: str | None = None, version: str | None = None) -> None:
        self.version = version
        self.path = path
        if self.path:
            _add_in_modules_path(self.path)
        self.loaded: dict[str, LoadedModule] = {}
        self.logger = getLogger(f"{__name__}.loader")

    def get_or_load_module(self, module_name: str) -> LoadedModule:
        """
        Can raise a ModuleLoadError exception.
        """
        if module_name not in self.loaded:
            self.load_module(module_name)
        return self.loaded[module_name]

    def iter_existing_module_names(self) -> Iterator[str]:
        try:
            import woob_modules
        except ImportError:
            return

        for module in pkgutil.iter_modules(woob_modules.__path__):
            if module.name.startswith("_") or module.name.endswith("_"):
                continue
            yield module.name

    def module_exists(self, name: str) -> bool:
        for existing_module_name in self.iter_existing_module_names():
            if existing_module_name == name:
                return True
        return False

    def load_all(self) -> None:
        for existing_module_name in self.iter_existing_module_names():
            try:
                self.load_module(existing_module_name)
            except ModuleLoadError as e:
                self.logger.warning("could not load module %s: %s", existing_module_name, e)

    def load_module(self, module_name: str) -> None:
        module_path = self.get_module_path(module_name)

        if module_name in self.loaded:
            self.logger.debug('Module "%s" is already loaded from %s', module_name, module_path)
            return

        if module_path:
            _add_in_modules_path(module_path)

        # Load spec for now to check version without trying to load the module,
        # as if it depends of an uninstalled dependence or a newest version of
        # woob, it may crash.
        module_spec = importlib.util.find_spec(f"woob_modules.{module_name}")
        if module_spec is None:
            raise ModuleLoadError(module_name, f"Module {module_name} does not exist")
        self.check_version(module_name, module_spec)

        try:
            pymodule = importlib.import_module(f"woob_modules.{module_name}")
            module = self.LOADED_MODULE(pymodule)
        except Exception as e:
            if logging.root.level <= logging.DEBUG:
                self.logger.exception(e)
            raise ModuleLoadError(module_name, str(e)) from e

        self.loaded[module_name] = module
        self.logger.debug(
            'Loaded module "%s" from %s'
            % (
                module.name,
                module.path,
            )
        )

    def get_module_path(self, module_name: str) -> str | None:
        return self.path

    def check_version(self, module_name: str, module_spec: ModuleSpec) -> None:
        woob_version = Version(self.version) if self.version else None

        if module_spec.origin is None:
            return

        # For a directory module, module_spec.origin is
        # 'woob_modules/bnp/__init__.py' so get 'woob_modules/bnp'.
        # For a single-file module, module_spec.origin is
        # 'woob_modules/bnp.py' so get 'woob_modules/'.
        # In that case, that's not a problem, we can assume the parent
        # requirements.txt file applies on all single-file modules.
        requirements_path = Path(module_spec.origin).parent / "requirements.txt"

        for name, spec in parse_requirements(requirements_path).items():
            if name == "woob":
                if woob_version and woob_version not in spec:
                    # specific user friendly error message
                    raise ModuleLoadError(
                        module_name,
                        f"Module requires woob {spec}, but you use woob {self.version}'.\n"
                        "Hint: use 'woob update' or install a newer version of woob",
                    )
                continue

            try:
                pkg = metadata.distribution(name)
            except metadata.PackageNotFoundError as exc:
                raise ModuleLoadError(
                    module_name, f'Module requires python package "{name}" but not installed.'
                ) from exc

            if Version(pkg.version) not in spec:
                raise ModuleLoadError(
                    module_name,
                    f'Module requires python package "{name}" {spec} but version {pkg.version} is installed',
                )


class RepositoryModulesLoader(ModulesLoader):
    """
    Load modules from repositories.
    """

    def __init__(self, repositories: Repositories) -> None:
        super().__init__(repositories.modules_dir, repositories.version)
        self.repositories = repositories
        # repositories.modules_dir is ...../woob_modules
        # shouldn't be in sys.path, its parent should
        # or we add it in woob_modules.__path__
        # sys.path.append(os.path.dirname(repositories.modules_dir))

    def iter_existing_module_names(self) -> Iterator[str]:
        yield from self.repositories.get_all_modules_info()

    def get_module_path(self, module_name: str) -> str:
        minfo = self.repositories.get_module_info(module_name)
        if minfo is None:
            raise ModuleLoadError(module_name, f"No such module {module_name}")
        if minfo.path is None:
            raise ModuleLoadError(module_name, f"Module {module_name} is not installed")

        return minfo.path
