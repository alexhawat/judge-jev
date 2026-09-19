# judge-jev (Rust runtime)

Production Rust runtime using tracing and a minreq HTTP client that matches typesafe-sdk Python wire format.

## Build & test

```bash
cargo build --release
cargo test
cargo run -- run --rubric assistant-reply --input ../fixtures/assistant-reply-pass.json --mock
```

## TypeSafe HTTP

- Endpoint: `POST https://api.typesafe.ai/v1/systemone`
- Auth: `Authorization: Bearer $TYPESAFE_API_KEY`
- Request body: `{ "state", "model", "questions" }` (question objects with `type`, `instructions`, `criteria`)
- Response: `{ "model", "usage": { "input_tokens", "output_tokens" }, "answers": { ... } }`

No official Rust SDK; this crate implements the documented shapes from typesafe-sdk 0.7.x.
