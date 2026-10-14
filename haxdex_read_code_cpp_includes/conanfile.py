from conan import ConanFile


class HaxdexReadCodeCppIncludesConan(ConanFile):
    name = "haxdex_read_code_cpp_includes"
    version = "0.1.0"
    package_type = "header-library"

    python_requires = "haxorg_conan_base/0.1.0"
    python_requires_extend = "haxorg_conan_base.HaxorgPackage"

    haxorg_package_libs = ()

    exports_sources = (
        "CMakeLists.txt",
        "src/*",
    )

    def requirements(self):
        self.requires(
            "haxdex_read_code_cpp_common/0.1.0",
            transitive_headers=True,
            transitive_libs=True,
        )
