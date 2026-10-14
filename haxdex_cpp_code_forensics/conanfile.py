from conan import ConanFile


class HaxdexCppCodeForensicsConan(ConanFile):
    name = "haxdex_cpp_code_forensics"
    version = "0.1.0"
    package_type = "application"

    python_requires = "haxorg_conan_base/0.1.0"
    python_requires_extend = "haxorg_conan_base.HaxorgPackage"
    provided_binaries = ["haxdex_cpp_code_forensics"]

    default_options = {
        "hwloc/*:shared": True,
        "hstd_cpp_lib/*:with_perfetto": True,
    }

    exports_sources = (
        "CMakeLists.txt",
        "cmake/*",
        "src/*",
        "tests/*",
    )

    def requirements(self):
        self.requires(
            "hstd_cpp_lib/0.1.0",
            transitive_headers=True,
            transitive_libs=True,
        )
