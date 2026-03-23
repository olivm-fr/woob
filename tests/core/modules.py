# Copyright(C) 2025 Gilles Dartiguelongue
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

# flake8: compatible

from __future__ import annotations

import logging
import pathlib
from importlib.machinery import ModuleSpec
from importlib.util import module_from_spec
from pkgutil import ModuleInfo
from unittest.mock import Mock, patch

import pytest

from woob import __version__
from woob.capabilities.base import Capability
from woob.core.modules import LoadedModule, ModulesLoader
from woob.exceptions import ModuleLoadError
from woob.tools.backend import Module


class TestCap(Capability): ...


class AnotherCap(Capability): ...


class SampleModule(Module):
    NAME = "sample"


class DetailedModule(Module, TestCap, AnotherCap):
    NAME = "detailed"
    VERSION = "1.0"
    MAINTAINER = "John Doe"
    EMAIL = "jdoe@example.com"


def test_loaded_module_load() -> None:
    """A single Woob module is allowed per Python package."""
    package = module_from_spec(ModuleSpec("placeholder", None))

    # No module in package
    with patch("woob.core.modules.getmodule", Mock(side_effect=[package])):
        with pytest.raises(ImportError, match=r"<module 'placeholder'> is not a backend \(no Module class found\)"):
            LoadedModule(package)

    # 1 module in package
    package.SampleModule = SampleModule  # type: ignore[attr-defined]
    assert "SampleModule" in dir(package)

    with patch("woob.core.modules.getmodule", Mock(side_effect=[package])):
        module = LoadedModule(package)

    assert module.klass is SampleModule

    # 2 modules in package
    package.DetailedModule = DetailedModule  # type: ignore[attr-defined]

    with patch("woob.core.modules.getmodule", Mock(side_effect=[package, package])):
        with pytest.raises(ImportError, match=r"At least two modules are defined"):
            LoadedModule(package)


def test_loaded_module_properties() -> None:
    """Test most attributes and capability related functions of LoadedModule instances."""
    pkg1 = module_from_spec(ModuleSpec("mod1", None))
    pkg1.DetailedModule = DetailedModule  # type: ignore[attr-defined]
    pkg2 = module_from_spec(ModuleSpec("mod2", None))
    pkg2.SampleModule = SampleModule  # type: ignore[attr-defined]

    with patch("woob.core.modules.getmodule", Mock(side_effect=[pkg1, pkg2])):
        mod1 = LoadedModule(pkg1)
        mod2 = LoadedModule(pkg2)

    assert mod1.name == "detailed"
    assert mod1.maintainer == "John Doe <jdoe@example.com>"
    assert mod1.version == __version__
    assert list(mod1.iter_caps()) == [TestCap, AnotherCap]
    assert mod1.has_caps("TestCap") is True
    assert mod1.has_caps(AnotherCap) is True

    assert mod2.name == "sample"
    assert mod2.maintainer == "<unspecified> <<unspecified>>"
    assert mod2.version == __version__
    assert list(mod2.iter_caps()) == []
    assert mod2.has_caps("TestCap") is False


def test_modules_loader_load_module(tmp_path: pathlib.Path) -> None:
    """Explicit load one module."""
    spec = ModuleSpec("woob_modules.detail", None)
    spec.origin = str(tmp_path / "module.py")
    mod = module_from_spec(spec)
    mod.DetailedModule = DetailedModule  # type: ignore[attr-defined]
    mod.__file__ = spec.origin

    loader = ModulesLoader(str(tmp_path))

    with (
        patch("woob.core.modules.getmodule", Mock(return_value=mod)),
        patch("woob.core.modules.importlib.util.find_spec", Mock(side_effect=[spec])),
        patch("woob.core.modules.importlib.import_module", Mock(return_value=mod)),
    ):
        loader.get_or_load_module("detailed")
        loader.get_or_load_module("detailed")  # second branch
    assert list(loader.loaded.keys()) == ["detailed"]
    assert loader.get_module_path("detailed") == str(tmp_path)


def test_modules_loader_module_no_exists(caplog: pytest.LogCaptureFixture, tmp_path: pathlib.Path) -> None:
    """Module does not exist at all."""
    loader = ModulesLoader()

    assert loader.module_exists("placeholder") is False
    with pytest.raises(ModuleLoadError, match=r"Module .+ does not exist"):
        loader.load_module("placeholder")
    assert list(loader.loaded.keys()) == []


def test_modules_loader_module_not_valid(caplog: pytest.LogCaptureFixture, tmp_path: pathlib.Path) -> None:
    """Module has no valid Woob module."""
    spec = ModuleSpec("woob_modules.invalid", None)
    spec.origin = str(tmp_path / "module.py")
    mod = module_from_spec(spec)
    mod.__file__ = spec.origin

    loader = ModulesLoader()
    with (
        patch("woob.core.modules.getmodule", Mock(side_effect=[mod])),
        patch("woob.core.modules.importlib.util.find_spec", Mock(side_effect=[spec])),
        patch("woob.core.modules.importlib.import_module", Mock(side_effect=[mod])),
    ):
        with pytest.raises(ModuleLoadError, match=r"<module .+> is not a backend"):
            loader.load_module("placeholder")

    assert list(loader.loaded.keys()) == []


def test_modules_loader_module_cannot_load(caplog: pytest.LogCaptureFixture, tmp_path: pathlib.Path) -> None:
    """Module exists but cannot be loaded."""
    loader = ModulesLoader()
    with patch(
        "woob.core.modules.pkgutil.iter_modules",
        Mock(return_value=[ModuleInfo(None, "placeholder", False)]),  # type: ignore[arg-type]  # this is a mock
    ):
        assert loader.module_exists("placeholder") is True

        with caplog.at_level(logging.WARNING):
            loader.load_all()
            assert caplog.records[0].message == "could not load module placeholder: Module placeholder does not exist"

    assert list(loader.loaded.keys()) == []
