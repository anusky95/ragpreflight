# Taxonomy Coverage — Garani 2026 RAG Failure Modes

**ragpreflight v0.1** coverage of the 33-mode taxonomy from:

> Garani, A. (2026). *A Systematic Taxonomy of Failure Modes in Retrieval-Augmented Generation Systems.*
> TrustNLP 2026. doi:[10.18653/v1/2026.trustnlp-main.27](https://doi.org/10.18653/v1/2026.trustnlp-main.27)

## Coverage summary

| Relationship | Count | Meaning |
|---|---|---|
| **direct** | 2 | Detector directly measures this failure mechanism |
| **proxy** | 2 | Detector uses a proxy signal; does not confirm the failure |
| **risk_signal** | 2 | Detector finds a correlated risk indicator; not a confirmed instance |
| **runtime_required** | 21 | Requires live system traces, outputs, or config to assess |
| **unsupported** | 6 | Not assessed in v0.1 |
| **Total** | **33** | All F1–F33 modes from the paper |

Saying "2 direct + 4 proxy/risk + 27 runtime/unsupported" is an honest strength, not a weakness. A tool that claims 33/33 detection is lying.

---

## Full F1–F33 coverage matrix

### Stage 1: Ingestion (F1–F4)

| Mode | Name | Paper evidence | v0.1 relationship | Notes |
|---|---|---|---|---|
| F1 | Outdated/Stale Data | Strong | **proxy** via file mtime | File age is not proof of content staleness |
| F2 | Missing/Incomplete Documents | Moderate | unsupported | Requires known expected coverage |
| F3 | Layout Parsing Errors | Strong | **direct** | Scanner assesses table/structure parsing quality |
| F4 | Multimodality Conversion Loss | Moderate | **risk_signal** | Scanned PDF detection flags conversion risk only |

### Stage 2: Representation (F5–F6)

| Mode | Name | Paper evidence | v0.1 relationship | Notes |
|---|---|---|---|---|
| F5 | Tokenization Fragmentation | Limited | unsupported | Requires tokenizer introspection |
| F6 | Embedding Drift / Model Mismatch | Limited | runtime_required | Requires embedding config + version metadata |

### Stage 3: Retrieval (F7–F12)

| Mode | Name | Paper evidence | v0.1 relationship | Notes |
|---|---|---|---|---|
| F7 | Chunking Boundary Errors | Strong | **direct** | Chunker assesses boundary quality and structural breaks |
| F8 | Domain Embedding Mismatch | Strong | unsupported | Requires domain-labeled retrieval benchmark |
| F9 | Multi-Hop Reasoning Gaps | Strong | unsupported | Requires multi-hop query traces |
| F10 | Graph RAG Trade-offs | Moderate | unsupported | Requires graph RAG system |
| F11 | Low Recall / Ranking Failures | Moderate | **proxy** | Synthetic retrieval simulation — not labeled recall@k |
| F12 | Position-of-Gold Bias | Strong | runtime_required | Requires live LLM context window |

### Stage 4: Generation (F13–F17)

| Mode | Name | Paper evidence | v0.1 relationship | Notes |
|---|---|---|---|---|
| F13 | Hallucination Despite Context | Strong | runtime_required | Requires generated outputs + faithfulness eval |
| F14 | Conflicting Information Unresolved | Strong | runtime_required | Requires generator outputs |
| F15 | Incomplete / Partial Answers | Moderate | runtime_required | Requires generated outputs |
| F16 | Incorrect Specificity | Moderate | runtime_required | Requires generated outputs |
| F17 | Wrong Output Format | Limited | runtime_required | Requires generated outputs + format spec |

### Stage 5: Evaluation (F18–F19)

| Mode | Name | Paper evidence | v0.1 relationship | Notes |
|---|---|---|---|---|
| F18 | Metric Inadequacy | Strong | unsupported | Meta-level; not a detector target |
| F19 | Lack of Continuous Monitoring | Limited | runtime_required | System-level infrastructure requirement |

### Stage 6: Deployment (F20–F25)

| Mode | Name | Paper evidence | v0.1 relationship | Notes |
|---|---|---|---|---|
| F20 | Latency / Cost Bottlenecks | Moderate | runtime_required | Requires live system metrics |
| F21 | Auditability Gaps | Moderate | runtime_required | Requires system logging infrastructure |
| F22 | Domain Portability Degradation | Moderate | runtime_required | Requires cross-domain benchmark |
| F23 | PII / Compliance Leaks | Moderate | **risk_signal** | PII presence in source text ≠ confirmed runtime leak |
| F24 | Prompt Sensitivity | Moderate | runtime_required | Requires prompt variation experiments |
| F25 | Authorization / Policy Enforcement Failures | Moderate | runtime_required | Requires access-control config + runtime traces |

### Stage 7: Agentic Orchestration (F26–F33)

All 8 agentic modes lack peer-reviewed benchmarks (paper-assigned: Limited evidence).
All require runtime agent traces to assess.

| Mode | Name | Paper evidence | v0.1 relationship |
|---|---|---|---|
| F26 | Planning Failures | Limited | runtime_required |
| F27 | Tool Selection and Execution Errors | Limited | runtime_required |
| F28 | Context Memory Degradation | Limited | runtime_required |
| F29 | Multi-Agent Coordination Failures | Limited | runtime_required |
| F30 | Recursive Hallucination Cascades | Limited | runtime_required |
| F31 | Unbounded Cost / Latency Spirals | Limited | runtime_required |
| F32 | Unsafe Reasoning Chains | Limited | runtime_required |
| F33 | Attribution and Governance Gaps | Limited | runtime_required |

---

## What "proxy" and "risk_signal" mean

**proxy** — The detector uses a correlating signal that *suggests* a risk but does not confirm the failure mode.

Example: `F1 Outdated/Stale Data` is flagged via filesystem `mtime`. A file modified 18 months ago *might* contain stale content, but content freshness is not established from modification time alone. The issue message always says: *"File-age freshness proxy. Content staleness is not established."*

**risk_signal** — The detector finds something that *co-occurs* with the failure mode in production but cannot confirm an instance of it.

Example: `F23 PII/Compliance Leaks` is flagged when sensitive identifiers (emails, SSNs, phone numbers) are found in source documents. Presence in source text does not establish that a runtime leak will occur — it depends on retrieval patterns, access controls, and LLM behaviour. The issue message always says: *"Presence does not establish a runtime leak."*

---

## What this audit cannot determine

The following modes require runtime evidence (live system traces, LLM outputs, agent logs) that ragpreflight cannot access from static documents:

- F6, F12, F13–F17, F19–F22, F24–F33

Static pre-ingestion scanning cannot catch hallucination, conflicting-info resolution, prompt sensitivity, latency, authorization failures, or any agentic failure mode.

For runtime coverage, see:
- [DeepEval](https://deepeval.com) — RAG/agent metrics, faithfulness, CI integration
- [Ragas](https://docs.ragas.io) — context precision/recall, faithfulness, response relevancy
- [RAGChecker](https://pypi.org/project/ragchecker/) — fine-grained retriever/generator diagnosis
- [Phoenix / OpenInference](https://github.com/Arize-ai/openinference) — traces, evaluations, annotations

---

*This matrix is grounded in the paper and will be updated as detectors are promoted from heuristic to benchmark-validated status.*
