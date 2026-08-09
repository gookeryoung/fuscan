//! fuscan-re：Rust + PyO3 原生匹配引擎。
//!
//! 将 fuscan 的 `match_content_via_buckets` 核心逻辑下沉到 Rust，
//! 通过 PyO3 `allow_threads` 释放 GIL，并用 `regex` crate（DFA + aho-corasick）
//! 替代 Python `re`，实现大文本复合正则的真正并行匹配。
//!
//! 语义等价：与 Python `match_content_via_buckets` 完全一致的命中结果
//! （first_match_text / total_count / detail），缺失时 fuscan 回退纯 Python。

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use regex::Regex;
use std::collections::{HashMap, HashSet};
use std::sync::RwLock;

// ============================================================================
// 常量与枚举
// ============================================================================

/// 字面量提取的最小长度（与 Python `_extract_literals` 默认值一致）。
/// 避免过短关键字（如单字母）导致高误报率。
const MIN_LITERAL_LEN: usize = 3;

/// Python `re` 模块的内联标志位掩码。
const RE_IGNORECASE: u32 = 2;
const RE_MULTILINE: u32 = 8;
const RE_DOTALL: u32 = 16;
const RE_VERBOSE: u32 = 64;

/// 叶子匹配模式（对应 Python `MatchMode` 枚举的 `.value` 字符串）。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
enum MatchMode {
    Regex,
    Contains,
    Equals,
    Startswith,
    Endswith,
}

impl MatchMode {
    /// 从 Python `MatchMode.value` 字符串解析。
    fn from_str(s: &str) -> Option<Self> {
        match s {
            "regex" => Some(MatchMode::Regex),
            "contains" => Some(MatchMode::Contains),
            "equals" => Some(MatchMode::Equals),
            "startswith" => Some(MatchMode::Startswith),
            "endswith" => Some(MatchMode::Endswith),
            _ => None,
        }
    }
}

// ============================================================================
// 辅助函数：内联标志解析、字面量提取、去子串
// ============================================================================

/// 提取正则模式开头的内联标志（如 `(?i)`、`(?im)`）。
///
/// 与 Python `_extract_inline_flags` 语义一致：将开头的 `(?[imsx]+)` 组提取出来，
/// 返回清理后的模式与标志位掩码。
fn extract_inline_flags(pattern: &str) -> (String, u32) {
    let bytes = pattern.as_bytes();
    let mut extracted: u32 = 0;
    let mut pos = 0;

    while pos + 3 < bytes.len() && bytes[pos] == b'(' && bytes[pos + 1] == b'?' {
        let mut flag_pos = pos + 2;
        let mut flags: u32 = 0;
        while flag_pos < bytes.len() {
            match bytes[flag_pos] {
                b'i' => flags |= RE_IGNORECASE,
                b'm' => flags |= RE_MULTILINE,
                b's' => flags |= RE_DOTALL,
                b'x' => flags |= RE_VERBOSE,
                b')' => break,
                _ => {
                    flags = 0;
                    break;
                }
            }
            flag_pos += 1;
        }
        if flags != 0 && flag_pos < bytes.len() && bytes[flag_pos] == b')' {
            extracted |= flags;
            pos = flag_pos + 1;
        } else {
            break;
        }
    }

    (pattern[pos..].to_string(), extracted)
}

/// 将标志位组合转换为内联标志字符串（如 `RE_IGNORECASE | RE_DOTALL` → `"is"`）。
fn flags_to_chars(flags: u32) -> String {
    let mut chars = String::new();
    if flags & RE_IGNORECASE != 0 {
        chars.push('i');
    }
    if flags & RE_MULTILINE != 0 {
        chars.push('m');
    }
    if flags & RE_DOTALL != 0 {
        chars.push('s');
    }
    if flags & RE_VERBOSE != 0 {
        chars.push('x');
    }
    chars
}

/// 从正则模式中提取字面量片段（长度 >= `min_len`）。
///
/// 使用 `regex-syntax` 解析 AST，提取所有「必然出现在匹配文本中」的字面量。
/// 与 Python `_extract_literals` + `_walk_sre_ast` 语义一致：
///
/// - `LITERAL`：累积到当前字面串
/// - `BRANCH`（`|`）：各分支独立递归，前缀继承
/// - `SUBPATTERN`（捕获组）：递归内部，前缀继承
/// - `MAX_REPEAT`（量词）：内部字面量可能不出现，前缀不传递
/// - `IN`（字符类）：若全部为单字面量则展开为候选前缀组合
fn extract_literals(pattern: &str, min_len: usize) -> Vec<String> {
    let mut parser = regex_syntax::ast::parse::Parser::new();
    let ast = match parser.parse(pattern) {
        Ok(ast) => ast,
        Err(_) => return vec![],
    };
    let mut literals = vec![];
    walk_ast(&ast, min_len, String::new(), &mut literals);
    // 去重，保留首次出现顺序
    let mut seen = HashSet::new();
    literals
        .into_iter()
        .filter(|l| l.chars().count() >= min_len)
        .filter(|l| seen.insert(l.clone()))
        .collect()
}

/// 递归遍历 AST 节点，提取字面量片段。
fn walk_ast(ast: &regex_syntax::ast::Ast, min_len: usize, prefix: String, literals: &mut Vec<String>) {
    use regex_syntax::ast::Ast;
    match ast {
        Ast::Empty(_) => {
            if prefix.chars().count() >= min_len {
                literals.push(prefix);
            }
        }
        Ast::Flags(_) => {
            // 标志节点不贡献字符，跳过（保留 prefix 不变）
            if prefix.chars().count() >= min_len {
                literals.push(prefix);
            }
        }
        Ast::Literal(lit) => {
            let mut current = prefix;
            current.push(lit.c);
            if current.chars().count() >= min_len {
                literals.push(current);
            }
        }
        Ast::Concat(concat) => {
            walk_concat(concat, min_len, prefix, literals);
        }
        Ast::Alternation(alt) => {
            // | 分支：先 flush 前缀，各分支继承前缀独立递归
            if prefix.chars().count() >= min_len {
                literals.push(prefix.clone());
            }
            for branch in &alt.asts {
                walk_ast(branch, min_len, prefix.clone(), literals);
            }
        }
        Ast::Group(group) => {
            // 捕获组：递归内部，前缀继承
            walk_ast(&group.ast, min_len, prefix, literals);
        }
        Ast::Repetition(rep) => {
            // 量词：内部字面量可能不出现，前缀先 flush，递归用空前缀
            if prefix.chars().count() >= min_len {
                literals.push(prefix.clone());
            }
            walk_ast(&rep.ast, min_len, String::new(), literals);
        }
        Ast::Dot(_)
        | Ast::Assertion(_)
        | Ast::ClassUnicode(_)
        | Ast::ClassPerl(_)
        | Ast::ClassBracketed(_) => {
            // `.` / 断言 / 字符类匹配范围不确定，无法提取确定字面量，flush prefix
            if prefix.chars().count() >= min_len {
                literals.push(prefix);
            }
        }
    }
}

/// 遍历 Concat 节点（顺序子表达式），累积连续字面量。
///
/// 与 Python `_walk_sre_ast` 的循环逻辑一致：
/// - `LITERAL`：累积到 `current`
/// - 非字面量：flush `current`，按节点类型递归（前缀传递/不传递）
fn walk_concat(
    concat: &regex_syntax::ast::Concat,
    min_len: usize,
    prefix: String,
    literals: &mut Vec<String>,
) {
    use regex_syntax::ast::Ast;
    let mut current = prefix;
    for sub in &concat.asts {
        match sub {
            Ast::Literal(lit) => {
                current.push(lit.c);
            }
            Ast::Flags(_) => {
                // 标志节点不贡献字符，跳过
            }
            Ast::Alternation(alt) => {
                // | 分支：先 flush current，各分支继承 current 递归
                if current.chars().count() >= min_len {
                    literals.push(current.clone());
                }
                for branch in &alt.asts {
                    walk_ast(branch, min_len, current.clone(), literals);
                }
                current.clear();
            }
            Ast::Group(group) => {
                // 捕获组：递归内部，前缀为 current
                walk_ast(&group.ast, min_len, current.clone(), literals);
                current.clear();
            }
            Ast::Repetition(rep) => {
                // 量词：flush current，递归用空前缀
                if current.chars().count() >= min_len {
                    literals.push(current.clone());
                }
                walk_ast(&rep.ast, min_len, String::new(), literals);
                current.clear();
            }
            Ast::Concat(sub_concat) => {
                // 嵌套 Concat：递归处理
                walk_concat(sub_concat, min_len, current.clone(), literals);
                current.clear();
            }
            _ => {
                // 其他节点（Dot 等）：flush current
                if current.chars().count() >= min_len {
                    literals.push(current.clone());
                }
                current.clear();
            }
        }
    }
    // flush 剩余累积
    if current.chars().count() >= min_len {
        literals.push(current);
    }
}

/// 去重并去子串：若 kw1 是 kw2 的子串，仅保留 kw2（kw2 命中时 kw1 必命中）。
///
/// 与 Python `_dedup_substrings` 语义一致：先按插入顺序去重，
/// 再按长度降序检查——若某关键字是已保留关键字的子串则丢弃。
fn dedup_substrings(keywords: Vec<String>) -> Vec<String> {
    // 按插入顺序去重
    let mut seen = HashSet::new();
    let unique: Vec<String> = keywords
        .into_iter()
        .filter(|kw| seen.insert(kw.clone()))
        .collect();
    // 按长度降序排序（稳定排序，同长度保留原顺序）
    let mut sorted: Vec<String> = unique;
    sorted.sort_by(|a, b| b.chars().count().cmp(&a.chars().count()));
    // 保留不是已保留关键字子串的项
    let mut kept: Vec<String> = Vec::with_capacity(sorted.len());
    for kw in sorted {
        let is_substring = kept.iter().any(|other: &String| other.contains(kw.as_str()));
        if !is_substring {
            kept.push(kw);
        }
    }
    kept
}

/// Python 风格的字符串 repr（单引号，转义特殊字符）。
///
/// 用于构造与 Python `f"...{text!r}"` 一致的 detail 字符串。
fn py_repr(s: &str) -> String {
    let mut result = String::with_capacity(s.len() + 2);
    result.push('\'');
    for c in s.chars() {
        match c {
            '\'' => result.push_str("\\'"),
            '\\' => result.push_str("\\\\"),
            '\n' => result.push_str("\\n"),
            '\r' => result.push_str("\\r"),
            '\t' => result.push_str("\\t"),
            _ => result.push(c),
        }
    }
    result.push('\'');
    result
}

// ============================================================================
// 桶数据结构
// ============================================================================

/// 单条规则的元信息（桶内规则）。
struct RuleInfo {
    rule_name: String,
    severity: String,
    description: String,
    /// 原始 pattern（未 escape）。
    /// - REGEX：正则模式（仅用于诊断，match_text 取实际捕获文本）
    /// - CONTAINS/EQUALS/STARTSWITH/ENDSWITH：原始子串，作为 match_text
    pattern: String,
}

/// 一组同 (mode, case_sensitive) 的顶层纯 CONTENT 规则。
///
/// 与 Python `_ContentRuleBucket` 结构对齐：
/// - 整桶复合 OR 正则 `compiled`
/// - 逐规则命名组子正则片段 `sub_parts`（供活跃子集动态拼接）
/// - 两级预筛关键字（桶级 + 逐规则）
/// - 活跃子集缓存（避免重复编译）
struct Bucket {
    mode: MatchMode,
    case_sensitive: bool,
    rules: Vec<RuleInfo>,
    /// 整桶复合 OR 正则：`(?P<_f0>...)|(?P<_f1>...)|...`
    compiled: Regex,
    /// 逐规则命名组子正则片段（含内联 flag 包装），下标与 rules 对齐。
    sub_parts: Vec<String>,
    /// 桶级预筛关键字（已去子串/去重）。
    prefilter_keywords: Vec<String>,
    /// 逐规则预筛关键字，下标与 rules 对齐。空列表表示该规则无可提取字面量。
    per_rule_keywords: Vec<Vec<String>>,
    /// 预筛是否大小写不敏感（桶 case_sensitive=False 或任一规则含 (?i)）。
    prefilter_case_insensitive: bool,
    /// 活跃子集复合正则缓存：sorted(active_idx) -> Option<Regex>。
    /// None 表示编译失败（回退到整桶 compiled）。
    active_cache: RwLock<HashMap<Vec<usize>, Option<Regex>>>,
}

impl Bucket {
    /// 获取仅含 `active_idx` 规则的复合正则（带缓存）。
    ///
    /// 与 Python `_get_active_compiled` 语义一致：
    /// - 全部活跃时复用整桶 `compiled`
    /// - 部分活跃时从 `sub_parts` 拼接子集，编译并缓存
    /// - 编译失败回退到整桶 `compiled`
    fn get_active_compiled(&self, active_idx: &[usize]) -> Regex {
        // 全部活跃：直接用整桶
        if active_idx.len() == self.rules.len() {
            return self.compiled.clone();
        }
        // 查缓存
        let key = active_idx.to_vec();
        {
            let cache = self.active_cache.read().unwrap();
            if let Some(cached) = cache.get(&key) {
                return cached.clone().unwrap_or_else(|| self.compiled.clone());
            }
        }
        // 缓存未命中：编译
        let parts: Vec<&str> = active_idx.iter().map(|&i| self.sub_parts[i].as_str()).collect();
        let pattern = parts.join("|");
        let flags_str = if self.case_sensitive { "" } else { "(?i)" };
        let full_pattern = format!("{}{}", flags_str, pattern);
        let compiled = Regex::new(&full_pattern).ok();
        // 写缓存
        {
            let mut cache = self.active_cache.write().unwrap();
            cache.insert(key, compiled.clone());
        }
        compiled.unwrap_or_else(|| self.compiled.clone())
    }
}

// ============================================================================
// Python 面向的类型
// ============================================================================

/// 从 Python 传入的规则规格（用于构建 ContentBucketEngine）。
///
/// Python 侧构造：
/// ```python
/// RuleSpec(rule_name="P001", severity="critical", description="AWS Key",
///          mode="regex", pattern="AKIA[0-9A-Z]{16}", case_sensitive=False)
/// ```
#[derive(FromPyObject)]
struct RuleSpec {
    rule_name: String,
    severity: String,
    description: String,
    mode: String,
    pattern: String,
    case_sensitive: bool,
}

/// 匹配命中结果（返回给 Python）。
///
/// Python 侧通过属性访问读取字段，再构造 `RuleHit`：
/// ```python
/// for hit in engine.match_content(content):
///     rule_hit = RuleHit(
///         rule_name=hit.rule_name, severity=Severity(hit.severity), ...
///     )
/// ```
#[pyclass]
struct RuleHitData {
    #[pyo3(get)]
    rule_name: String,
    #[pyo3(get)]
    severity: String,
    #[pyo3(get)]
    detail: String,
    #[pyo3(get)]
    match_text: String,
    #[pyo3(get)]
    match_count: usize,
    #[pyo3(get)]
    target: String,
    #[pyo3(get)]
    match_texts: Vec<String>,
    #[pyo3(get)]
    match_description: String,
}

/// 原生 CONTENT 桶匹配引擎。
///
/// 替代 Python `match_content_via_buckets`，核心改进：
/// - `regex` crate（DFA + aho-corasick）替代 Python `re`
/// - PyO3 `allow_threads` 释放 GIL，多 worker 线程真正并行
/// - 两级预筛 + 活跃子集缓存与 Python 实现语义一致
#[pyclass]
struct ContentBucketEngine {
    buckets: Vec<Bucket>,
}

#[pymethods]
impl ContentBucketEngine {
    /// 从规则规格列表构建匹配引擎。
    ///
    /// 自动按 (mode, case_sensitive) 分桶，构建复合 OR 正则与预筛关键字。
    /// 单条规则的桶（无合并收益）不会被创建（与 Python `build_content_buckets` 一致）。
    ///
    /// :param rule_specs: 规则规格列表，每条含 rule_name/severity/description/
    ///                    mode/pattern/case_sensitive 六个字段
    #[new]
    fn new(rule_specs: Vec<RuleSpec>) -> PyResult<Self> {
        let buckets = build_buckets(rule_specs)?;
        Ok(ContentBucketEngine { buckets })
    }

    /// 对内容执行匹配，返回命中列表。
    ///
    /// 匹配期间通过 `py.allow_threads` 释放 GIL，允许多 worker 线程真正并行。
    /// 结果与 Python `match_content_via_buckets` 完全一致。
    ///
    /// :param content: 文件文本内容
    /// :return: RuleHitData 列表，每个桶内每条规则最多产出一条聚合命中
    fn match_content<'py>(
        &self,
        py: Python<'py>,
        content: &str,
    ) -> PyResult<Vec<Bound<'py, RuleHitData>>> {
        // 克隆 content 到 owned String，使闭包满足 Send 约束（释放 GIL 期间不借用 Python 对象）
        let content_owned = content.to_string();
        // 释放 GIL 执行纯 Rust 匹配
        let rust_results: Vec<RuleHitData> =
            py.detach(move || self.match_content_inner(&content_owned));
        // 重新获取 GIL，将 Rust 结果转为 Python 对象
        let mut py_results = Vec::with_capacity(rust_results.len());
        for hit in rust_results {
            let py_hit = Py::new(py, hit)?;
            py_results.push(py_hit.into_bound(py));
        }
        Ok(py_results)
    }

    /// 返回桶数量（供 Python 侧诊断/测试）。
    #[getter]
    fn bucket_count(&self) -> usize {
        self.buckets.len()
    }
}

impl ContentBucketEngine {
    /// 核心匹配逻辑（纯 Rust，不持 GIL）。
    ///
    /// 与 Python `match_content_via_buckets` 语义一致：
    /// 1. 桶级预筛：整桶关键字均不在 content 中 → 跳过
    /// 2. 逐规则预筛：计算活跃规则子集
    /// 3. CONTAINS case_sensitive 快路径：用 `str::matches().count()` 替代 finditer
    /// 4. 正则路径：活跃子集动态编译 + finditer + lastgroup 分派
    fn match_content_inner(&self, content: &str) -> Vec<RuleHitData> {
        let mut hits: Vec<RuleHitData> = Vec::new();

        // 是否需要小写化 content（任一桶 prefilter_case_insensitive 时计算一次）
        let needs_lower = self.buckets.iter().any(|b| b.prefilter_case_insensitive);
        let content_lower: Option<String> = if needs_lower {
            Some(content.to_lowercase())
        } else {
            None
        };

        for bucket in &self.buckets {
            // 选择预筛 haystack
            let haystack: &str = if bucket.prefilter_case_insensitive {
                content_lower.as_ref().unwrap().as_str()
            } else {
                content
            };

            // 桶级快速短路：整桶关键字均不在 content 中 → 全部规则必不命中
            if !bucket.prefilter_keywords.is_empty()
                && !bucket
                    .prefilter_keywords
                    .iter()
                    .any(|kw| haystack.contains(kw.as_str()))
            {
                continue;
            }

            // 逐规则预筛：计算活跃规则下标
            let active_idx: Vec<usize> = bucket
                .rules
                .iter()
                .enumerate()
                .filter(|(i, _)| {
                    let kws = &bucket.per_rule_keywords[*i];
                    kws.is_empty() || kws.iter().any(|kw| haystack.contains(kw.as_str()))
                })
                .map(|(i, _)| i)
                .collect();

            if active_idx.is_empty() {
                continue;
            }

            // CONTAINS case_sensitive 快路径：用 count 替代 finditer
            if bucket.mode == MatchMode::Contains && bucket.case_sensitive {
                for &idx in &active_idx {
                    let pat = &bucket.rules[idx].pattern;
                    if pat.is_empty() {
                        continue;
                    }
                    let cnt = content.matches(pat.as_str()).count();
                    if cnt > 0 {
                        hits.push(RuleHitData {
                            rule_name: bucket.rules[idx].rule_name.clone(),
                            severity: bucket.rules[idx].severity.clone(),
                            detail: format!("包含 {}", py_repr(pat)),
                            match_text: pat.clone(),
                            match_count: cnt,
                            target: "content".to_string(),
                            match_texts: vec![pat.clone()],
                            match_description: bucket.rules[idx].description.clone(),
                        });
                    }
                }
                continue;
            }

            // 获取活跃子集复合正则
            let compiled = bucket.get_active_compiled(&active_idx);

            // 跑一次 captures_iter，按命名组分派到各规则
            // per_rule: rule_idx -> (first_match_text, total_count)
            let mut per_rule: HashMap<usize, (String, usize)> = HashMap::new();
            let active_set: HashSet<usize> = active_idx.iter().copied().collect();

            for caps in compiled.captures_iter(content) {
                // 遍历捕获组，找到第一个匹配的命名组
                for (group_idx, name) in compiled.capture_names().enumerate() {
                    if group_idx == 0 {
                        continue; // 跳过 group 0（整体匹配）
                    }
                    if let Some(name) = name {
                        // 解析命名组名 _f{idx} 得到规则下标
                        if let Some(rule_idx) = parse_group_name(name) {
                            if !active_set.contains(&rule_idx) {
                                continue;
                            }
                            if let Some(m) = caps.get(group_idx) {
                                let txt = m.as_str().to_string();
                                let entry = per_rule
                                    .entry(rule_idx)
                                    .or_insert_with(|| (txt.clone(), 0usize));
                                entry.1 += 1;
                                break; // 每个 capture 只有一个命名组匹配
                            }
                        }
                    }
                }
            }

            // 构造 RuleHitData
            for (idx, (first_txt, total_cnt)) in per_rule {
                let rule = &bucket.rules[idx];
                let (detail, match_text) = match bucket.mode {
                    MatchMode::Regex => (
                        format!("正则命中: {}", py_repr(&first_txt)),
                        first_txt,
                    ),
                    MatchMode::Contains => (
                        format!("包含 {}", py_repr(&rule.pattern)),
                        rule.pattern.clone(),
                    ),
                    MatchMode::Equals => (
                        "完全相等".to_string(),
                        rule.pattern.clone(),
                    ),
                    MatchMode::Startswith => (
                        format!("以 {} 开头", py_repr(&rule.pattern)),
                        rule.pattern.clone(),
                    ),
                    MatchMode::Endswith => (
                        format!("以 {} 结尾", py_repr(&rule.pattern)),
                        rule.pattern.clone(),
                    ),
                };
                // 与 Python `(first_txt,) if first_txt else ()` 一致：空 match_text 返回空 Vec
                let match_texts = if match_text.is_empty() {
                    Vec::new()
                } else {
                    vec![match_text.clone()]
                };
                hits.push(RuleHitData {
                    rule_name: rule.rule_name.clone(),
                    severity: rule.severity.clone(),
                    detail,
                    match_text,
                    match_count: total_cnt,
                    target: "content".to_string(),
                    match_texts,
                    match_description: rule.description.clone(),
                });
            }
        }
        hits
    }
}

/// 解析命名组名 `_f{idx}` 得到规则下标。
fn parse_group_name(name: &str) -> Option<usize> {
    name.strip_prefix("_f").and_then(|s| s.parse::<usize>().ok())
}

/// 从规则规格列表构建桶。
///
/// 与 Python `build_content_buckets` 语义一致：
/// 1. 按 (mode, case_sensitive) 分组
/// 2. 单条规则的组跳过（无合并收益）
/// 3. 构建 OR 复合正则 `(?P<_f0>...)|(?P<_f1>...)|...`
/// 4. 提取预筛关键字（桶级 + 逐规则）
fn build_buckets(specs: Vec<RuleSpec>) -> PyResult<Vec<Bucket>> {
    // 按 (mode, case_sensitive) 分组
    let mut grouped: HashMap<(MatchMode, bool), Vec<RuleSpec>> = HashMap::new();
    for spec in specs {
        let mode = MatchMode::from_str(&spec.mode)
            .ok_or_else(|| PyValueError::new_err(format!("未知匹配模式: {}", spec.mode)))?;
        grouped.entry((mode, spec.case_sensitive)).or_default().push(spec);
    }

    let mut buckets: Vec<Bucket> = Vec::new();
    for ((mode, case_sensitive), mut group_specs) in grouped {
        if group_specs.len() <= 1 {
            // 单条规则无合并收益，跳过（Python 侧会丢回 remaining 走独立匹配）
            continue;
        }
        // 按 rule_name 排序保证稳定性
        group_specs.sort_by(|a, b| a.rule_name.cmp(&b.rule_name));

        let mut rules: Vec<RuleInfo> = Vec::with_capacity(group_specs.len());
        let mut sub_parts: Vec<String> = Vec::with_capacity(group_specs.len());
        let mut all_prefilter_keywords: Vec<String> = Vec::new();
        let mut per_rule_keywords: Vec<Vec<String>> = Vec::with_capacity(group_specs.len());
        let mut has_inline_ignorecase = false;
        let mut parts: Vec<String> = Vec::with_capacity(group_specs.len());

        for (i, spec) in group_specs.iter().enumerate() {
            // 根据 mode 生成子正则片段
            let sub: String = match mode {
                MatchMode::Regex => spec.pattern.clone(),
                MatchMode::Contains => regex::escape(&spec.pattern),
                MatchMode::Equals => format!("^{}$", regex::escape(&spec.pattern)),
                MatchMode::Startswith => format!("^{}", regex::escape(&spec.pattern)),
                MatchMode::Endswith => format!("{}$", regex::escape(&spec.pattern)),
            };
            // 提取内联标志并用 (?flag:...) 包装
            let (sub_clean, sub_flags) = extract_inline_flags(&sub);
            if sub_flags & RE_IGNORECASE != 0 {
                has_inline_ignorecase = true;
            }
            let grp_name = format!("_f{}", i);
            let part = if sub_flags != 0 {
                let flag_str = flags_to_chars(sub_flags);
                format!("(?{}:(?P<{}>{}))", flag_str, grp_name, sub_clean)
            } else {
                format!("(?P<{}>{})", grp_name, sub_clean)
            };
            parts.push(part.clone());
            sub_parts.push(part);

            // 提取字面量作为预筛关键字
            let literals = extract_literals(&sub_clean, MIN_LITERAL_LEN);
            all_prefilter_keywords.extend(literals.clone());
            per_rule_keywords.push(literals);

            // 保存原始 pattern（所有模式共用：CONTAINS/EQUALS/STARTSWITH/ENDSWITH 用作 match_text）
            rules.push(RuleInfo {
                rule_name: spec.rule_name.clone(),
                severity: spec.severity.clone(),
                description: spec.description.clone(),
                pattern: spec.pattern.clone(),
            });
        }

        // 编译整桶复合 OR 正则
        let flags_str = if case_sensitive { "" } else { "(?i)" };
        let full_pattern = format!("{}{}", flags_str, parts.join("|"));
        let compiled = Regex::new(&full_pattern).map_err(|e| {
            PyValueError::new_err(format!("桶复合正则编译失败 (mode={:?}): {}", mode, e))
        })?;

        // 设置预筛关键字大小写规则
        let prefilter_ci = !case_sensitive || has_inline_ignorecase;
        let prefilter_keywords: Vec<String> = if prefilter_ci {
            let lower_all: Vec<String> = all_prefilter_keywords
                .into_iter()
                .map(|kw| kw.to_lowercase())
                .collect();
            dedup_substrings(lower_all)
        } else {
            dedup_substrings(all_prefilter_keywords)
        };
        let per_rule_keywords_final: Vec<Vec<String>> = if prefilter_ci {
            per_rule_keywords
                .into_iter()
                .map(|kws| {
                    let lower: Vec<String> = kws.into_iter().map(|kw| kw.to_lowercase()).collect();
                    dedup_substrings(lower)
                })
                .collect()
        } else {
            per_rule_keywords.into_iter().map(dedup_substrings).collect()
        };

        buckets.push(Bucket {
            mode,
            case_sensitive,
            rules,
            compiled,
            sub_parts,
            prefilter_keywords,
            per_rule_keywords: per_rule_keywords_final,
            prefilter_case_insensitive: prefilter_ci,
            active_cache: RwLock::new(HashMap::new()),
        });
    }
    Ok(buckets)
}

// ============================================================================
// PyO3 模块定义
// ============================================================================

/// Python 可调用的字面量提取函数（供测试/诊断）。
#[pyfunction]
fn extract_literals_py(pattern: &str, min_len: Option<usize>) -> Vec<String> {
    extract_literals(pattern, min_len.unwrap_or(MIN_LITERAL_LEN))
}

/// Python 可调用的内联标志提取函数（供测试/诊断）。
#[pyfunction]
fn extract_inline_flags_py(pattern: &str) -> (String, u32) {
    extract_inline_flags(pattern)
}

/// Python 可调用的去子串函数（供测试/诊断）。
#[pyfunction]
fn dedup_substrings_py(keywords: Vec<String>) -> Vec<String> {
    dedup_substrings(keywords)
}

/// fuscan-re：Rust + PyO3 原生匹配引擎。
///
/// 提供 `ContentBucketEngine` 类，替代 Python `match_content_via_buckets`，
/// 释放 GIL 实现真正并行的正则匹配。
#[pymodule]
fn fuscan_re(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<ContentBucketEngine>()?;
    m.add_class::<RuleHitData>()?;
    m.add_function(wrap_pyfunction!(extract_literals_py, m)?)?;
    m.add_function(wrap_pyfunction!(extract_inline_flags_py, m)?)?;
    m.add_function(wrap_pyfunction!(dedup_substrings_py, m)?)?;
    Ok(())
}

// ============================================================================
// Rust 单元测试
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_extract_inline_flags() {
        assert_eq!(extract_inline_flags("(?i)abc"), ("abc".to_string(), RE_IGNORECASE));
        assert_eq!(extract_inline_flags("(?im)abc"), ("abc".to_string(), RE_IGNORECASE | RE_MULTILINE));
        assert_eq!(extract_inline_flags("abc"), ("abc".to_string(), 0));
        assert_eq!(extract_inline_flags("(?i)(?m)abc"), ("abc".to_string(), RE_IGNORECASE | RE_MULTILINE));
        assert_eq!(extract_inline_flags("(abc)"), ("(abc)".to_string(), 0));
    }

    #[test]
    fn test_flags_to_chars() {
        assert_eq!(flags_to_chars(RE_IGNORECASE), "i");
        assert_eq!(flags_to_chars(RE_IGNORECASE | RE_DOTALL), "is");
        assert_eq!(flags_to_chars(0), "");
    }

    #[test]
    fn test_extract_literals_simple() {
        let lits = extract_literals("password=", 3);
        assert!(lits.contains(&"password=".to_string()));

        let lits = extract_literals("AKIA[0-9A-Z]{16}", 3);
        assert!(lits.contains(&"AKIA".to_string()));
    }

    #[test]
    fn test_extract_literals_branch() {
        let lits = extract_literals("password|passwd|pwd", 3);
        assert!(lits.contains(&"password".to_string()));
        assert!(lits.contains(&"passwd".to_string()));
        // "pwd" is len 3, should be present
        assert!(lits.contains(&"pwd".to_string()));
    }

    #[test]
    fn test_dedup_substrings() {
        let input = vec![
            "password".to_string(),
            "pass".to_string(),
            "password".to_string(), // duplicate
            "key".to_string(),
        ];
        let result = dedup_substrings(input);
        // "pass" is substring of "password", should be removed
        assert!(result.contains(&"password".to_string()));
        assert!(!result.contains(&"pass".to_string()));
        assert!(result.contains(&"key".to_string()));
    }

    #[test]
    fn test_py_repr() {
        assert_eq!(py_repr("hello"), "'hello'");
        assert_eq!(py_repr("a\\b"), "'a\\\\b'");
        assert_eq!(py_repr("a\nb"), "'a\\nb'");
    }

    #[test]
    fn test_parse_group_name() {
        assert_eq!(parse_group_name("_f0"), Some(0));
        assert_eq!(parse_group_name("_f42"), Some(42));
        assert_eq!(parse_group_name("_f"), None);
        assert_eq!(parse_group_name("other"), None);
    }
}
