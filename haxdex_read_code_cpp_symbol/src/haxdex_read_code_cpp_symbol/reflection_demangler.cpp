
#include <llvm/ADT/Hashing.h>
#include <llvm/ADT/SmallBitVector.h>
#include <llvm/DebugInfo/DWARF/DWARFContext.h>
#include <llvm/DebugInfo/Symbolize/Symbolize.h>
#include <llvm/Demangle/Demangle.h>
#include <llvm/Demangle/ItaniumDemangle.h>
#include <llvm/IR/LLVMContext.h>
#include <llvm/IR/Module.h>
#include <llvm/IRReader/IRReader.h>
#include <llvm/Object/Archive.h>
#include <llvm/Object/Binary.h>
#include <llvm/Object/ObjectFile.h>
#include <llvm/Object/SymbolSize.h>
#include <llvm/ProfileData/Coverage/CoverageMappingReader.h>
#include <llvm/ProfileData/InstrProfReader.h>
#include <llvm/ProfileData/InstrProfWriter.h>
#include <llvm/Support/CommandLine.h>
#include <llvm/Support/Error.h>
#include <llvm/Support/JSON.h>
#include <llvm/Support/MD5.h>
#include <llvm/Support/Regex.h>
#include <llvm/Support/SourceMgr.h>
#include <llvm/Support/TargetSelect.h>
#include <llvm/Support/VirtualFileSystem.h>
#include <llvm/Support/raw_ostream.h>

#include <absl/hash/hash.h>
#include <absl/log/log.h>
#include <boost/describe.hpp>

#include <hstd_cpp_lib/logger/perfetto_aux.hpp>
#include <hstd_cpp_lib/stdlib/Filesystem.hpp>
#include <hstd_cpp_lib/stdlib/algorithms/Enumerate.hpp>
#include <hstd_cpp_lib/stdlib/containers/Map.hpp>
#include <hstd_cpp_lib/stdlib/containers/algorithms.hpp>
#include <hstd_cpp_lib/stdlib/formatting/Formatter.hpp>
#include <hstd_cpp_lib/stdlib/formatting/specializations/OptFormatter.hpp>
#include <hstd_cpp_lib/stdlib/serde/Json.hpp>
#include <hstd_cpp_lib/stdlib/serde/JsonSerde.hpp>
#include <hstd_cpp_lib/system/aux_utils.hpp>
#include <hstd_cpp_lib/system/macros.hpp>

#include <haxdex_read_code_cpp/reflection_demangler.hpp>
#include <haxdex_read_code_cpp/reflection_perf.hpp>

BinarySymComponentId demangle_to_db(
    Node const*               node,
    BinarySymbolVisitContext& ctx,
    BinaryFileDB&             db) {
    if (node == nullptr) { return BinarySymComponentId::Nil(); }

    BinarySymComponent comp;
    comp.binary_sym_kind = static_cast<int>(node->getKind());
    hstd::Vec<BinarySymComponentId> nested;

    using BSF = BinarySymField;
    using Res = std::optional<BinarySymField>;
    using Met = std::optional<visit_meta>;

    auto visit_value = hstd::overloaded{
        [&](std::string_view value, Met const& meta) -> Res {
            return BSF{std::string{value.begin(), value.end()}};
        },
        [&](std::string value, Met const& meta) -> Res { return BSF{value}; },
        [&](bool const& value, Met const& meta) -> Res { return BSF{hstd::fmt1(value)}; },
        [&](Node const* value, Met const& meta) -> Res {
            nested.push_back(demangle_to_db(value, ctx, db));
            if (meta && meta->head_node) {
                return BSF{demangle_to_json(value, ctx, 0, meta->head_depth.value_or(1))};
            } else {
                return std::nullopt;
            }
        },
        [&](NodeArray const& value, Met const& meta) -> Res {
            for (auto const& it : value) {
                nested.push_back(demangle_to_db(it, ctx, db));
            }
            return std::nullopt;
        },
        [&](Qualifiers const& value, Met const& meta) -> Res {
            return BSF{hstd::fmt1(value)};
        },
        [&](FunctionRefQual const& value, Met const& meta) -> Res {
            return BSF{hstd::fmt1(value)};
        },
        [&](Node::Prec const& value, Met const& meta) -> Res {
            return BSF{hstd::fmt1(value)};
        },
        [&](TemplateParamKind const& value, Met const& meta) -> Res {
            return BSF{hstd::fmt1(value)};
        },
        [&](SpecialSubKind const& value, Met const& meta) -> Res {
            return BSF{hstd::fmt1(value)};
        },
        [&](llvm::itanium_demangle::ReferenceKind const& value, Met const& meta) -> Res {
            return BSF{hstd::fmt1(value)};
        },
    };

    auto sub = [&](std::string const&               field,
                   auto const&                      value,
                   std::optional<visit_meta> const& meta = std::nullopt) {
        auto info = visit_value(value, meta);
        if (info) { comp.head_direct_fields.insert_or_assign(field, info.value()); }
    };

    comp.head_direct_fields["Kind"] = BSF{std::string{boost::describe::enum_to_string(
        static_cast<KindProxy>(node->getKind()), "<none>")}};

    visit_node_fields(node, sub);
    auto res = db.symbol_components.add(comp);
    if (!nested.empty()) { db.nested_symbols.insert_or_assign(res, nested); }
    return res;
}

BinarySymComponentId parseBinarySymbolName(
    std::string const&        name,
    BinaryFileDB&             db,
    BinarySymbolVisitContext& ctx) {
    Demangler Parser(name.data(), name.data() + name.size());
    Node*     AST = Parser.parse();
    __perf_trace("sym", "Parse binary symbol");
    BinarySymComponentId res = BinarySymComponentId::Nil();
    {
        __perf_trace("sym", "Demangle to JSON", "name", name);
        res = demangle_to_db(AST, ctx, db);
    }

    // In some cases LLVM fails to demangle itanium names for
    // lambdas and some other symbols, like
    // `SemOrgApi.cpp:_ZZNK3sem7Subtree14getTimePeriodsE6IntSetINS0_6Period4KindEEENK3$_0clINS0_8Property17ExportLatexHeaderEEEDaRKT_`
    // so this heuristics is used to give more information on the
    // failure -- `cxx-filt` also fails to process this, so it
    // seems to be a LLVM issue.
    if (AST == nullptr && name.contains("$_") && name.contains("cl")
        && name.contains('K')) {
        // pass
    } else if (AST == nullptr) {
        // pass
    }

    return res;
}

std::size_t hash_json_value(llvm::json::Value const& value) {
    std::size_t result = 0;

    switch (value.kind()) {
        case llvm::json::Value::Null: hstd::hax_hash_combine(result, 0); break;

        case llvm::json::Value::Boolean:
            hstd::hax_hash_combine(result, value.getAsBoolean().value());
            break;

        case llvm::json::Value::Number:
            hstd::hax_hash_combine(result, value.getAsNumber().value());
            break;

        case llvm::json::Value::String:
            hstd::hax_hash_combine(result, llvm::hash_value(value.getAsString().value()));
            break;

        case llvm::json::Value::Array: {
            auto const& array = *value.getAsArray();
            hstd::hax_hash_combine(result, array.size());
            for (const auto& element : array) {
                hstd::hax_hash_combine(result, hash_json_value(element));
            }
            break;
        }

        case llvm::json::Value::Object: {
            auto const& object = *value.getAsObject();
            std::vector<std::pair<std::string, const llvm::json::Value*>> sorted_fields;

            for (const auto& pair : object) {
                sorted_fields.emplace_back(pair.first.str(), &pair.second);
            }

            std::sort(
                sorted_fields.begin(),
                sorted_fields.end(),
                [](auto const& a, auto const& b) { return a.first < b.first; });

            hstd::hax_hash_combine(result, sorted_fields.size());
            for (const auto& field : sorted_fields) {
                hstd::hax_hash_combine(result, field.first);
                hstd::hax_hash_combine(result, hash_json_value(*field.second));
            }
            break;
        }
    }

    return result;
}

bool json_values_equal(llvm::json::Value const& lhs, llvm::json::Value const& rhs) {
    if (lhs.kind() != rhs.kind()) { return false; }

    switch (lhs.kind()) {
        case llvm::json::Value::Null: return true;

        case llvm::json::Value::Boolean:
            return lhs.getAsBoolean().value() == rhs.getAsBoolean().value();

        case llvm::json::Value::Number:
            return lhs.getAsNumber().value() == rhs.getAsNumber().value();

        case llvm::json::Value::String:
            return lhs.getAsString().value() == rhs.getAsString().value();

        case llvm::json::Value::Array: {
            auto const& lhs_array = *lhs.getAsArray();
            auto const& rhs_array = *rhs.getAsArray();

            if (lhs_array.size() != rhs_array.size()) { return false; }

            for (int i = 0; i < lhs_array.size(); ++i) {
                if (!json_values_equal(lhs_array[i], rhs_array[i])) { return false; }
            }
            return true;
        }

        case llvm::json::Value::Object: {
            auto const& lhs_object = *lhs.getAsObject();
            auto const& rhs_object = *rhs.getAsObject();

            if (lhs_object.size() != rhs_object.size()) { return false; }

            for (const auto& pair : lhs_object) {
                auto rhs_it = rhs_object.find(pair.first);
                if (rhs_it == rhs_object.end()) { return false; }
                if (!json_values_equal(pair.second, rhs_it->second)) { return false; }
            }
            return true;
        }
    }

    return false;
}

std::size_t std::hash<BinarySymComponent>::operator()(
    BinarySymComponent const& it) const noexcept {
    std::size_t result = 0;
    hstd::hax_hash_combine(result, it.binary_sym_kind);

    std::vector<std::string> sorted_keys;
    for (auto const& [key, value] : it.head_direct_fields) {
        sorted_keys.emplace_back(key);
    }
    std::sort(sorted_keys.begin(), sorted_keys.end());

    for (auto const& key : sorted_keys) {
        hstd::hax_hash_combine(result, key);
        hstd::hax_hash_combine(result, it.head_direct_fields.at(key));
    }

    return result;
}

std::size_t std::hash<BinarySymField>::operator()(
    BinarySymField const& it) const noexcept {
    return std::visit(
        hstd::overloaded{
            [](llvm::json::Value const& value) { return hash_json_value(value); },
            [](auto const& val) { return std::hash<std::decay_t<decltype(val)>>{}(val); },
        },
        it.value);
}

bool BinarySymComponent::operator==(BinarySymComponent const& other) const noexcept {
    if (binary_sym_kind != other.binary_sym_kind) { return false; }

    if (head_direct_fields.size() != other.head_direct_fields.size()) { return false; }

    std::vector<std::string> lhs_keys;
    for (auto const& [key, value] : head_direct_fields) { lhs_keys.emplace_back(key); }
    std::sort(lhs_keys.begin(), lhs_keys.end());

    std::vector<std::string> rhs_keys;
    for (auto const& [key, value] : other.head_direct_fields) {
        rhs_keys.emplace_back(key);
    }
    std::sort(rhs_keys.begin(), rhs_keys.end());

    for (int i = 0; i < static_cast<int>(lhs_keys.size()); ++i) {
        if (lhs_keys.at(i) != rhs_keys.at(i)) { return false; }
        if (head_direct_fields.at(lhs_keys.at(i))
            != other.head_direct_fields.at(rhs_keys.at(i))) {
            return false;
        }
    }

    return true;
}

namespace {

struct BinaryFileCtx {
    int counter     = 0;
    int min_counter = 0;
    int max_counter = INT_MAX;
    // Aggregate sections across all processed object files (including
    // archive members)
    std::map<std::string, std::vector<BinarySymbolInfoId>> sectionSymbols;
    BinaryFileDB                                           db;
    BinarySymbolVisitContext                               ctx;
};

void processObjectFile(
    llvm::object::ObjectFile&         obj,
    std::optional<std::string> const& objectLabel,
    BinaryFileCtx&                    ctx) {
    // Create DWARF context once per object file (cheap enough, and
    // avoids per-symbol reparse)
    std::unique_ptr<llvm::DWARFContext> dwarf = llvm::DWARFContext::create(obj);

    llvm::DILineInfoSpecifier spec;
    spec.FNKind  = llvm::DILineInfoSpecifier::FunctionNameKind::LinkageName;
    spec.FLIKind = llvm::DILineInfoSpecifier::FileLineInfoKind::AbsoluteFilePath;

    for (const auto& symAndSize : llvm::object::computeSymbolSizes(obj)) {
        __perf_trace("sym", "Process symbol");
        ++ctx.counter;
        if (ctx.counter < ctx.min_counter) { continue; }
        if (ctx.max_counter < ctx.counter) { break; }

        llvm::object::SymbolRef const& symbol = symAndSize.first;
        uint64_t                       size   = symAndSize.second;
        if (size == 0) { continue; }

        auto nameOrErr = symbol.getName();
        if (!nameOrErr) {
            consumeError(nameOrErr.takeError());
            continue;
        }
        std::string name = nameOrErr->str();

        auto addressOrErr = symbol.getAddress();
        if (!addressOrErr) {
            consumeError(addressOrErr.takeError());
            continue;
        }
        uint64_t address = *addressOrErr;

        // Section name (optional)
        std::string sectionName;
        auto        sectionOrErr = symbol.getSection();
        if (sectionOrErr) {
            llvm::object::section_iterator section = *sectionOrErr;
            if (section != obj.section_end()) {
                if (auto sectionNameOrErr = section->getName()) {
                    sectionName = sectionNameOrErr->str();
                } else {
                    consumeError(sectionNameOrErr.takeError());
                }
            }
        } else {
            consumeError(sectionOrErr.takeError());
        }

        std::string demangled = llvm::demangle(name);

        BinarySymbolInfo info{
            .name      = name,
            .demangled = demangled,
            .head      = parseBinarySymbolName(name, ctx.db, ctx.ctx),
            .size      = size,
            .address   = address,
        };

        // Debug location (DWARF): best-effort. Mostly meaningful
        // for code symbols. Skip undefined symbols or zero
        // addresses to avoid junk lookups.
        bool isUndef = false;
        if (auto flagsOrErr = symbol.getFlags()) {
            isUndef = ((*flagsOrErr) & llvm::object::SymbolRef::SF_Undefined) != 0;

        } else {
            consumeError(flagsOrErr.takeError());
        }

        if (!isUndef && address != 0) {
            llvm::object::SectionedAddress sa;
            sa.Address = address;

            std::optional<llvm::DILineInfo> li = dwarf->getLineInfoForAddress(sa, spec);
            if (li && li->Line != 0 && !li->FileName.empty()) {
                BinarySymbolDebugLocation loc;
                loc.file     = li->FileName;
                loc.line     = li->Line;
                loc.column   = li->Column;
                loc.function = li->FunctionName;
                info.debug   = std::move(loc);
            }
        }

        ctx.sectionSymbols[sectionName].push_back(ctx.db.symbols.add(info));
    }
}
} // namespace

BinaryFileDB getSymbolsInBinary(std::string const& path) {
    __perf_trace("sym", "Get symbols in binary");
    llvm::InitializeNativeTarget();
    llvm::InitializeNativeTargetAsmParser();
    llvm::InitializeNativeTargetAsmPrinter();
    llvm::InitializeNativeTargetDisassembler();

    auto binaryOrErr = llvm::object::createBinary(path);
    if (!binaryOrErr) {
        auto errorMsg = llvm::toString(binaryOrErr.takeError());
        throw hstd::runtime_error::init(
            fmt::format("Binary loading error: {}", errorMsg));
    }

    llvm::object::OwningBinary<llvm::object::Binary> binary = std::move(*binaryOrErr);
    llvm::object::Binary*                            bin    = binary.getBinary();

    BinaryFileCtx ctx{};

    if (auto sym_min = std::getenv("REFLECTION_TOOL_SYM_MIN")) {
        ctx.min_counter = std::atoi(sym_min);
    }
    if (auto sym_max = std::getenv("REFLECTION_TOOL_SYM_MAX")) {
        ctx.max_counter = std::atoi(sym_max);
    }

    // Dispatch based on binary kind
    if (auto* obj = llvm::dyn_cast<llvm::object::ObjectFile>(bin)) {
        processObjectFile(*obj, /*objectLabel=*/path, ctx);
    } else if (auto* arch = llvm::dyn_cast<llvm::object::Archive>(bin)) {
        llvm::Error err = llvm::Error::success();
        for (auto const& child : arch->children(err)) {
            std::optional<std::string> memberLabel;

            if (auto nameOrErr = child.getName()) {
                // Label like: "libfoo.a(member.o)"
                memberLabel = path + "(" + nameOrErr->str() + ")";
            } else {
                consumeError(nameOrErr.takeError());
                memberLabel = path + "(<unknown-member>)";
            }

            auto childBinOrErr = child.getAsBinary();
            if (!childBinOrErr) {
                consumeError(childBinOrErr.takeError());
                continue;
            }

            llvm::object::Binary* childBin = childBinOrErr->get();
            if (auto* childObj = llvm::dyn_cast<llvm::object::ObjectFile>(childBin)) {
                processObjectFile(*childObj, memberLabel, ctx);
            } else {
                // else: skip non-object members
            }
        }
        if (err) { consumeError(std::move(err)); }
    } else {
        throw hstd::invalid_argument::init(
            "Unsupported binary type (not ObjectFile or Archive)");
    }

    // Build db.sections from accumulated map
    for (const auto& [sectionName, symbols] : ctx.sectionSymbols) {
        ctx.db.sections.push_back({sectionName, symbols});
    }

    return ctx.db;
}

NO_COVERAGE void CreateTables(SQLite::Database& db) {
    auto path       = hstd::fs::path{__FILE__}.parent_path() / "reflection_demangler.sql";
    std::string sql = hstd::readFile(path);
    db.exec(sql);
}

namespace {
/// \brief Create a text for a `INSERT` statement with a given list of
/// column names and identical number of coverage instantiation
NO_COVERAGE std::string SqlInsert(
    std::string const&              Table,
    std::vector<std::string> const& Columns) {
    std::string result = fmt::format("INSERT INTO {} (", Table);
    for (auto it : llvm::enumerate(Columns)) {
        if (it.index() != 0) { result += ", "; }
        result += it.value();
    }

    result += ") VALUES (";

    for (auto it : llvm::enumerate(Columns)) {
        if (it.index() != 0) { result += ", "; }
        result += "?";
    }

    result += ")";
    return result;
}
} // namespace

struct queries {
    SQLite::Statement demangled_head;
    SQLite::Statement demangled_nested;
    SQLite::Statement binary_sections;
    SQLite::Statement binary_symbol;

    queries(SQLite::Database& db)
        : // ---
        demangled_head(
            db,
            SqlInsert(
                "DemangledHead",
                {
                    "Id",     // 1
                    "Kind",   // 2
                    "Fields", // 3
                }))
        ,
        // ---
        demangled_nested(
            db,
            SqlInsert(
                "DemangledNested",
                {
                    "ComponentIndex", // 1
                    "Parent",         // 2
                    "Self",           // 3
                }))
        ,
        // ---
        binary_sections(
            db,
            SqlInsert(
                "BinarySection",
                {
                    "Id",   // 1
                    "Name", // 2
                }))
        ,
        // ---
        binary_symbol(
            db,
            SqlInsert(
                "BinarySymbol",
                {
                    "Id",              // 1
                    "Name",            // 2
                    "Demangled",       // 3
                    "DemangledHeadId", // 4
                    "Size",            // 5
                    "Address",         // 6
                    "Section",         // 7
                    "File",            // 8
                    "Line",            // 9
                    "Column",          // 10
                    "Function",        // 11
                }))
    //
    {};
};

NO_COVERAGE void BinaryFileDB::writeToFile(std::string const& path) {
    hstd::fs::path db_file{path};

    if (hstd::fs::exists(db_file)) { hstd::fs::remove(db_file); }

    SQLite::Database db{db_file.native(), SQLite::OPEN_READWRITE | SQLite::OPEN_CREATE};
    CreateTables(db);
    queries q{db};

    __perf_trace_begin("sym", "Insert symbol components");
    for (auto const& [id, cp_value] : symbol_components.pairs()) {
        auto value = const_cast<BinarySymComponent*>(cp_value);
        q.demangled_head.bind(1, static_cast<int64_t>(id.getValue()));
        q.demangled_head.bind(2, static_cast<int>(value->binary_sym_kind));
        llvm::json::Object j_object;
        for (auto& [field_name, field_value] : value->head_direct_fields) {
            std::visit(
                hstd::overloaded{
                    [&](llvm::json::Value& value) {
                        j_object[field_name] = std::move(value);
                    },
                    [&](int const& value) { j_object[field_name] = value; },
                    [&](std::string const& value) { j_object[field_name] = value; },
                },
                field_value.value);
        }

        auto j_format = llvm::formatv("{0}", llvm::json::Value(std::move(j_object)));

        q.demangled_head.bind(3, j_format);

        q.demangled_head.exec();
        q.demangled_head.reset();
    }
    __perf_trace_end("sym");

    __perf_trace_begin("sym", "Insert sections");
    for (auto const& it : hstd::enumerator(sections)) {
        q.binary_sections.bind(1, it.index());
        q.binary_sections.bind(2, it.value().name);
        q.binary_sections.exec();
        q.binary_sections.reset();

        for (auto const& sym_id : it.value().symbols) {
            q.binary_symbol.bind(1, static_cast<int64_t>(sym_id.getValue()));
            auto const& sym = symbols.at(sym_id);
            q.binary_symbol.bind(2, sym.name);
            q.binary_symbol.bind(3, sym.demangled);
            q.binary_symbol.bind(4, static_cast<int64_t>(sym.head.getValue()));
            q.binary_symbol.bind(5, static_cast<int>(sym.size));
            q.binary_symbol.bind(6, static_cast<int>(sym.address));
            q.binary_symbol.bind(7, static_cast<int>(it.index()));
            if (sym.debug) {
                auto const& d = sym.debug.value();
                q.binary_symbol.bind(8, d.file);
                q.binary_symbol.bind(9, static_cast<int>(d.line));
                q.binary_symbol.bind(10, static_cast<int>(d.column));
                if (!d.function.empty()) { q.binary_symbol.bind(11, d.function); }
            }
            q.binary_symbol.exec();
            q.binary_symbol.reset();
        }
    }
    __perf_trace_end("sym");

    __perf_trace_begin("sym", "Insert nested symbols");
    for (auto const& id : hstd::sorted(nested_symbols.keys())) {
        for (auto const& it : hstd::enumerator(nested_symbols.at(id))) {
            q.demangled_nested.bind(1, static_cast<int>(it.index()));
            q.demangled_nested.bind(2, static_cast<int64_t>(id.getValue()));
            if (!it.value().isNil()) {
                q.demangled_nested.bind(3, static_cast<int64_t>(it.value().getIndex()));
            }
            q.demangled_nested.exec();
            q.demangled_nested.reset();
        }
    }
    __perf_trace_end("sym");
}

NO_COVERAGE void run_binary_symbols_collection(ReflectionCLI const& cli) {
    __perf_trace("main", "Process binary symbols");
    if (cli.output.empty()) {
        throw std::invalid_argument(
            fmt::format("Missing output path for reflection run"));
    }

    auto db = getSymbolsInBinary(cli.input.at(0));
    db.writeToFile(cli.output);
}
