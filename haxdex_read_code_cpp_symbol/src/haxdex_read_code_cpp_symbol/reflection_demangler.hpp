#pragma once

#include <SQLiteCpp/SQLiteCpp.h>
#include <hstd_cpp_lib/stdlib/Str.hpp>
#include <hstd_cpp_lib/stdlib/dod/dod_base_intern.hpp>
#include <hstd_cpp_lib/system/macros.hpp>
#include <hstd_cpp_lib/system/reflection.hpp>
#include <llvm/ADT/StringMap.h>
#include <llvm/Support/JSON.h>

#include <hstd_cpp_lib/stdlib/containers/Variant.hpp>
#include <hstd_cpp_lib/stdlib/dod/dod_base.hpp>

#include <haxdex_read_code_cpp_common/demangle_json.hpp>

DECL_ID_TYPE(BinarySymComponent, BinarySymComponentId, std::size_t);

struct BinarySymField {
    std::variant<int, std::string, llvm::json::Value> value;

    bool operator==(BinarySymField const& other) const noexcept {
        return value == other.value;
    }
};

struct BinarySymComponent {
    using id_type = BinarySymComponentId;
    std::unordered_map<std::string, BinarySymField> head_direct_fields;
    int                                             binary_sym_kind;

    bool operator==(BinarySymComponent const& other) const noexcept;
};

template <>
struct std::hash<BinarySymField> {
    std::size_t operator()(BinarySymField const& it) const noexcept;
};

template <>
struct std::hash<BinarySymComponent> {
    std::size_t operator()(BinarySymComponent const& it) const noexcept;
};

struct BinarySymbolDebugLocation {
    std::string file;
    uint32_t    line   = 0;
    uint32_t    column = 0;
    std::string function;

    DESC_FIELDS(BinarySymbolDebugLocation, (file, line, column, function));
};

struct BinarySymbolInfo {
    std::string                              name;
    std::string                              demangled;
    BinarySymComponentId                     head;
    uint64_t                                 size;
    uint64_t                                 address;
    std::optional<BinarySymbolDebugLocation> debug;
    DESC_FIELDS(BinarySymbolInfo, (name, demangled, head, size, address, debug));
};

DECL_ID_TYPE(BinarySymbolInfo, BinarySymbolInfoId, std::size_t);

struct BinarySectionInfo {
    std::string                     name;
    std::vector<BinarySymbolInfoId> symbols;
    DESC_FIELDS(BinarySectionInfo, (name, symbols));
};

struct BinaryFileDB {
    hstd::UnorderedMap<BinarySymComponentId, hstd::Vec<BinarySymComponentId>>
        nested_symbols;

    hstd::dod::InternStore<BinarySymComponentId, BinarySymComponent> symbol_components;

    hstd::dod::Store<BinarySymbolInfoId, BinarySymbolInfo> symbols;
    std::vector<BinarySectionInfo>                         sections;

    void writeToFile(std::string const& path);
};

BinaryFileDB         getSymbolsInBinary(std::string const& path);
BinarySymComponentId parseBinarySymbolName(
    std::string const&        name,
    BinaryFileDB&             db,
    BinarySymbolVisitContext& ctx);
