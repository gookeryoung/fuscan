//! OLE 复合文档流提取模块。
//!
//! 用 cfb crate 替代 Python olefile 的 OLE 流读取，通过 PyO3 ``py.detach``
//! 释放 GIL，允许多 worker 线程并行解析。
//!
//! 语义等价：与 Python ``olefile.OleFileIO(data).openstream(name).read()``
//! 一致——返回指定流的字节内容，流不存在时返回 None。fuscan-core 缺失时
//! fuscan 回退 olefile。

use cfb::CompoundFile;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::io::{Cursor, Read};
use std::path::PathBuf;

/// 从 OLE 复合文档中读取指定流的字节内容。
///
/// 用 cfb crate 解析 OLE 复合文档，返回指定流的字节内容。流不存在时返回
/// ``None``，与 Python ``olefile.OleFileIO.exists(name)`` 行为一致。
///
/// 释放 GIL：``py.detach`` 使多 worker 线程可并行调用，不被 GIL 序列化。
///
/// :param data: OLE 复合文档完整字节
/// :param stream_name: 流名称（如 ``"WordDocument"`` / ``"PowerPoint Document"``）
/// :return: 流的字节内容；流不存在时返回 None
/// :raises ValueError: OLE 复合文档解析失败（cfb crate 错误）
#[pyfunction]
pub fn extract_ole_stream(
    py: Python<'_>,
    data: &[u8],
    stream_name: &str,
) -> PyResult<Option<Vec<u8>>> {
    // 克隆到 owned，使闭包满足 Send 约束（释放 GIL 期间不借用 Python 对象）
    let data_owned = data.to_vec();
    let stream_name_owned = stream_name.to_string();
    py.detach(move || extract_ole_stream_inner(&data_owned, &stream_name_owned))
}

/// OLE 流提取核心逻辑（纯 Rust，不持 GIL）。
///
/// ``cfb::CompoundFile::open`` 要求底层 ``Read + Seek``，``Cursor<Vec<u8>>``
/// 满足约束。流路径用绝对路径（如 ``/WordDocument``），由 ``stream_name_to_path``
/// 从 Python 侧的相对名称转换而来。
fn extract_ole_stream_inner(data: &[u8], stream_name: &str) -> PyResult<Option<Vec<u8>>> {
    let cursor = Cursor::new(data);
    let mut comp = match CompoundFile::open(cursor) {
        Ok(c) => c,
        Err(e) => {
            return Err(PyValueError::new_err(format!(
                "OLE 复合文档解析失败: {}",
                e
            )));
        }
    };

    // cfb 使用绝对路径（如 "/WordDocument"），stream_name 是相对名称
    let path = stream_name_to_path(stream_name);
    if !comp.exists(&path) {
        return Ok(None);
    }

    let mut stream = match comp.open_stream(&path) {
        Ok(s) => s,
        Err(e) => {
            return Err(PyValueError::new_err(format!(
                "OLE 流 '{}' 打开失败: {}",
                stream_name, e
            )));
        }
    };

    let mut buffer = Vec::new();
    match stream.read_to_end(&mut buffer) {
        Ok(_) => Ok(Some(buffer)),
        Err(e) => Err(PyValueError::new_err(format!(
            "OLE 流 '{}' 读取失败: {}",
            stream_name, e
        ))),
    }
}

/// 将流名称转换为 cfb 路径（绝对路径，根存储下的子项）。
///
/// Python 侧传入相对名称（如 ``"WordDocument"``），cfb 要求绝对路径
/// （如 ``"/WordDocument"``）。含空格的名称（如 ``"PowerPoint Document"``）
/// 同样支持，cfb 内部按 path 组件解析。
fn stream_name_to_path(stream_name: &str) -> PathBuf {
    let mut path = PathBuf::from("/");
    path.push(stream_name);
    path
}

// ============================================================================
// Rust 单元测试
// ============================================================================

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Cursor, Write};

    /// 构建一个最小 CFB 文件，含指定流名与内容。
    ///
    /// 用 cfb crate 的 create API 在内存中写入流，返回完整 CFB 字节。
    fn build_cfb_with_stream(stream_name: &str, content: &[u8]) -> Vec<u8> {
        let mut buf: Cursor<Vec<u8>> = Cursor::new(Vec::new());
        {
            let mut comp = CompoundFile::create(&mut buf).expect("创建 CFB 失败");
            let mut path = PathBuf::from("/");
            path.push(stream_name);
            let mut stream = comp.create_stream(&path).expect("创建流失败");
            stream.write_all(content).expect("写入流失败");
            stream.flush().expect("flush 失败");
        }
        buf.into_inner()
    }

    #[test]
    fn test_stream_name_to_path_simple() {
        let path = stream_name_to_path("WordDocument");
        assert_eq!(path, PathBuf::from("/WordDocument"));
    }

    #[test]
    fn test_stream_name_to_path_with_space() {
        // PowerPoint Document 流名含空格
        let path = stream_name_to_path("PowerPoint Document");
        assert_eq!(path, PathBuf::from("/PowerPoint Document"));
    }

    #[test]
    fn test_extract_stream_found() {
        let content = b"Hello password world".to_vec();
        let data = build_cfb_with_stream("WordDocument", &content);
        let result = extract_ole_stream_inner(&data, "WordDocument").unwrap();
        assert_eq!(result, Some(content));
    }

    #[test]
    fn test_extract_stream_with_space_in_name() {
        // PowerPoint Document 流名含空格，验证 cfb 路径解析正确
        let content = b"slide content".to_vec();
        let data = build_cfb_with_stream("PowerPoint Document", &content);
        let result = extract_ole_stream_inner(&data, "PowerPoint Document").unwrap();
        assert_eq!(result, Some(content));
    }

    #[test]
    fn test_extract_stream_not_found() {
        let data = build_cfb_with_stream("WordDocument", b"content");
        let result = extract_ole_stream_inner(&data, "NonexistentStream").unwrap();
        assert_eq!(result, None);
    }

    #[test]
    fn test_extract_empty_stream() {
        let data = build_cfb_with_stream("WordDocument", b"");
        let result = extract_ole_stream_inner(&data, "WordDocument").unwrap();
        assert_eq!(result, Some(Vec::new()));
    }

    #[test]
    fn test_extract_invalid_cfb_raises() {
        // 非 CFB 字节应返回 PyValueError
        let result = extract_ole_stream_inner(b"not a cfb file", "WordDocument");
        assert!(result.is_err());
    }

    #[test]
    fn test_extract_empty_data_raises() {
        // 空字节非合法 CFB，应返回 PyValueError
        let result = extract_ole_stream_inner(b"", "WordDocument");
        assert!(result.is_err());
    }

    #[test]
    fn test_extract_utf16le_content_roundtrip() {
        // 模拟 DOC 提取场景：写入 UTF-16LE 编码的中文文本，读回后应为原始字节
        let text = "密码 password 测试";
        let content = text.encode_utf16().flat_map(|u| u.to_le_bytes()).collect::<Vec<u8>>();
        let data = build_cfb_with_stream("WordDocument", &content);
        let result = extract_ole_stream_inner(&data, "WordDocument").unwrap();
        assert_eq!(result, Some(content));
    }

    #[test]
    fn test_extract_multiple_streams_isolate_target() {
        // 多流场景：CFB 含 WordDocument 与 Data，仅读取目标流
        let mut buf: Cursor<Vec<u8>> = Cursor::new(Vec::new());
        let word_content = b"word secret".to_vec();
        let data_content = b"data payload".to_vec();
        let expected_word = word_content.clone();
        let expected_data = data_content.clone();
        {
            let mut comp = CompoundFile::create(&mut buf).expect("创建 CFB 失败");
            let mut word_path = PathBuf::from("/");
            word_path.push("WordDocument");
            let mut stream = comp.create_stream(&word_path).expect("创建 WordDocument 失败");
            stream.write_all(&word_content).expect("写入失败");
            stream.flush().expect("flush 失败");

            let mut data_path = PathBuf::from("/");
            data_path.push("Data");
            let mut stream = comp.create_stream(&data_path).expect("创建 Data 失败");
            stream.write_all(&data_content).expect("写入失败");
            stream.flush().expect("flush 失败");
        }
        let data = buf.into_inner();

        let result = extract_ole_stream_inner(&data, "WordDocument").unwrap();
        assert_eq!(result, Some(expected_word));
        let result = extract_ole_stream_inner(&data, "Data").unwrap();
        assert_eq!(result, Some(expected_data));
        let result = extract_ole_stream_inner(&data, "Missing").unwrap();
        assert_eq!(result, None);
    }
}
