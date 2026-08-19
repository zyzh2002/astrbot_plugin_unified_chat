use pyo3::prelude::*;

/// Split text into chunks with overlap. Aggressive, allocation-aware.
#[pyfunction]
fn chunk_text(text: &str, chunk_size: usize, chunk_overlap: usize) -> Vec<String> {
    if chunk_size == 0 {
        return vec![];
    }
    if text.is_empty() {
        return vec![];
    }
    let overlap = chunk_overlap.min(chunk_size.saturating_sub(1));
    let step = chunk_size - overlap;
    let chars: Vec<char> = text.chars().collect();
    let mut out = Vec::new();
    let mut start = 0usize;
    while start < chars.len() {
        let end = (start + chunk_size).min(chars.len());
        let s: String = chars[start..end].iter().collect();
        if !s.trim().is_empty() {
            out.push(s);
        }
        if end == chars.len() {
            break;
        }
        start += step;
    }
    out
}

/// Simple dedup helper: returns true if key was already seen (caller manages set).
/// Kept as stub for future LRU/Bloom acceleration.
#[pyfunction]
fn score_importance(char_len: usize, recency_hours: f64, freq: usize) -> f64 {
    // Placeholder scoring: will be tuned with real heuristics.
    let len_score = (char_len as f64 / 512.0).min(1.0) * 0.3;
    let recency = (-recency_hours / 72.0).exp() * 0.4;
    let freq_score = (freq as f64 / 10.0).min(1.0) * 0.3;
    (len_score + recency + freq_score).clamp(0.0, 1.0)
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(chunk_text, m)?)?;
    m.add_function(wrap_pyfunction!(score_importance, m)?)?;
    Ok(())
}
