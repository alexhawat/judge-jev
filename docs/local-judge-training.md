# Local judge training is a separate milestone

This repository now proposes routing thresholds and Jev instruction wording. It does
not ship trained weights or claim that a local model was trained.

A local-judge or pre-filter milestone needs a substantially larger human-labelled
corpus with train, held-out, and frozen adversarial splits; documented label policy
and adjudication; licenses that permit training; privacy review and deletion handling;
GPU capacity; a reproducible training stack; model and tokenizer version pins;
asymmetric reward/cost evaluation; red-team hard gates; calibration by domain and
stakes; latency, throughput, and inference-cost measurements; artifact signing and
rollback; and ongoing drift monitoring.

The cheaper first experiment is a defer-capable pre-filter: handle only cases where a
small model meets a conservative confidence policy and send everything else to Jev.
That experiment still requires the evidence above. Threshold search results and GEPA
prompt proposals are not model weights and cannot establish local-model quality.
