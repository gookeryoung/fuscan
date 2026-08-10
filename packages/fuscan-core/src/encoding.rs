//! 编码检测与解码模块。
//!
//! 用 encoding_rs + chardetng 替代 Python charset-normalizer 的编码检测，
//! 通过 PyO3 ``py.detach`` 释放 GIL，允许多 worker 线程并行解码。
//!
//! 语义等价：与 Python ``charset_normalizer.from_bytes(data).best()`` 后取
//! ``str()`` 一致——BOM 优先，统计检测兜底，无效字节用 U+FFFD 替换。
//! fuscan-core 缺失时 fuscan 回退 charset-normalizer。

use chardetng::EncodingDetector;
use encoding_rs::Encoding;
use pyo3::prelude::*;

/// 从字节检测编码并解码为字符串。
///
/// 检测优先级：
/// 1. **BOM**（UTF-8/UTF-16LE/UTF-16BE）：``Encoding::for_bom`` 命中即直接解码。
/// 2. **统计检测**（chardetng）：BOM 未命中时用 chardetng 猜测编码。
///
/// 解码用 encoding_rs，无效字节用 U+FFFD 替换（与 WHATWG Encoding Standard
/// 及 Python ``bytes.decode(encoding, errors="replace")`` 一致）。
///
/// 释放 GIL：``py.detach`` 使多 worker 线程可并行调用，不被 GIL 序列化。
///
/// :param data: 完整文件字节
/// :return: 解码后的字符串（始终非空，无效字节替换为 U+FFFD）
#[pyfunction]
pub fn decode_bytes(py: Python<'_>, data: &[u8]) -> String {
    // 克隆到 owned Vec，使闭包满足 Send 约束（释放 GIL 期间不借用 Python 对象）
    let data_owned = data.to_vec();
    py.detach(move || decode_bytes_inner(&data_owned))
}

/// 编码检测+解码核心逻辑（纯 Rust，不持 GIL）。
///
/// BOM 优先 → chardetng 统计检测 → encoding_rs 解码。
fn decode_bytes_inner(data: &[u8]) -> String {
    if data.is_empty() {
        return String::new();
    }

    // BOM 快路径：UTF-8-SIG / UTF-16LE / UTF-16BE
    // for_bom 返回 (encoding, bom_len)，decode 会自动剥离 BOM
    if let Some((encoding, _)) = Encoding::for_bom(data) {
        let (decoded, _, _) = encoding.decode(data);
        return decoded.into_owned();
    }

    // 统计检测：chardetng 猜测编码
    // - tld=None：本地文件无 TLD 上下文
    // - allow_utf8=true：允许猜测 UTF-8（text.py 快路径已过滤严格 UTF-8，
    //   到此处的数据非合法 UTF-8，但 chardetng 可能仍猜测 UTF-8 并用替换字符解码）
    let mut detector = EncodingDetector::new();
    detector.feed(data, true);
    let encoding = detector.guess(None, true);
    let (decoded, _, _) = encoding.decode(data);
    decoded.into_owned()
}

// ============================================================================
// Rust 单元测试
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_decode_utf8() {
        let data = "你好 world".as_bytes();
        let result = decode_bytes_inner(data);
        assert_eq!(result, "你好 world");
    }

    #[test]
    fn test_decode_utf8_bom() {
        let data = b"\xef\xbb\xbfUTF-8 with BOM";
        let result = decode_bytes_inner(data);
        assert_eq!(result, "UTF-8 with BOM");
    }

    #[test]
    fn test_decode_utf16_le_bom() {
        let text = "UTF-16 LE 中文";
        let mut data = vec![0xff, 0xfe]; // UTF-16LE BOM
        data.extend(text.encode_utf16().flat_map(|u| u.to_le_bytes()));
        let result = decode_bytes_inner(&data);
        assert_eq!(result, text);
    }

    #[test]
    fn test_decode_gbk() {
        // GBK 编码的中文（无 BOM，非合法 UTF-8）
        // 用足够长的文本给 chardetng 统计检测足够的特征
        let text = "这是一个包含密码字段的配置文件，密码为 password123，请妥善保管。";
        let data = text.encode_gbk();
        let result = decode_bytes_inner(&data);
        assert!(result.contains("password123"));
        assert!(result.contains("密码"));
    }

    #[test]
    fn test_decode_empty() {
        assert_eq!(decode_bytes_inner(b""), "");
    }

    #[test]
    fn test_decode_invalid_bytes_replaced() {
        // 孤立续字节（非法 UTF-8），解码应产生替换字符而非 panic
        let result = decode_bytes_inner(b"\x80\x81\xfd");
        assert!(!result.is_empty());
    }

    /// GBK 编码辅助（测试用）。
    trait GbkEncode {
        fn encode_gbk(&self) -> Vec<u8>;
    }

    impl GbkEncode for str {
        fn encode_gbk(&self) -> Vec<u8> {
            let mut result = Vec::new();
            for c in self.chars() {
                let s = c.to_string();
                if s.is_ascii() {
                    result.push(s.as_bytes()[0]);
                } else {
                    let (encoded, _, _) = encoding_rs::GBK.encode(&s);
                    result.extend_from_slice(&encoded);
                }
            }
            result
        }
    }
}
