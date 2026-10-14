from pathlib import Path

from conan import ConanFile, Workspace
from conan.tools.cmake import CMakeDeps, CMakeToolchain, cmake_layout


class MonorepoConan(ConanFile):
    settings = "os", "arch", "compiler", "build_type"

    def layout(self):
        cmake_layout(self)

    def generate(self):
        CMakeDeps(self).generate()

        toolchain = CMakeToolchain(self)
        toolchain.user_presets_path = str(
            Path(__file__).resolve().parent / "CMakeUserPresets.json"
        )
        toolchain.cache_variables["CMAKE_EXPORT_COMPILE_COMMANDS"] = True
        toolchain.generate()


class MonorepoWorkspace(Workspace):
    def root_conanfile(self):
        return MonorepoConan
