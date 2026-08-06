"""One-use corrections for Gemini integration validation."""

from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{path}: expected one correction anchor, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "src/e2h/gemini_generate_content.py",
    '                candidate_index = candidate.get("index", candidate_position)\n'
    "                message_id = record.candidate_ids[candidate_position]\n",
    "                message_id = record.candidate_ids[candidate_position]\n",
)

path = Path("src/e2h/gemini_generate_content.py")
text = path.read_text(encoding="utf-8")
old_validation = '''                content = candidate.get("content")
                if not isinstance(content, dict):
                    continue
                candidate_parts = _parts(content.get("parts"), "candidate.content.parts")
'''
new_validation = '''                candidate_content = candidate.get("content")
                if not isinstance(candidate_content, dict):
                    continue
                candidate_parts = _parts(
                    candidate_content.get("parts"), "candidate.content.parts"
                )
'''
old_import = '''            content = candidate.get("content")
            if isinstance(content, dict):
                parts = _parts(content.get("parts"), "candidate.content.parts")
'''
new_import = '''            candidate_content = candidate.get("content")
            if isinstance(candidate_content, dict):
                parts = _parts(
                    candidate_content.get("parts"), "candidate.content.parts"
                )
'''
for old, new in ((old_validation, new_validation), (old_import, new_import)):
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"expected one candidate mapping anchor, found {count}")
    text = text.replace(old, new, 1)

old_signature = '''def _content_signature(role: str, parts: list[dict[str, Any]]) -> str:
    payload = {"role": role, "parts": [_part_descriptor(part) for part in parts]}
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
'''
new_signature = '''def _signature_part(part: dict[str, Any]) -> dict[str, Any]:
    descriptor = _part_descriptor(part)
    kind = descriptor["type"]
    if kind == "thought":
        return descriptor
    if kind == "text":
        return {"type": "text", "text": part.get("text")}
    if kind == "function_call":
        call = cast(dict[str, Any], _pick(part, "function_call", "functionCall"))
        return {
            **descriptor,
            "args": _safe(call.get("args", {})),
        }
    if kind == "function_response":
        response = cast(
            dict[str, Any],
            _pick(part, "function_response", "functionResponse"),
        )
        return {
            **descriptor,
            "response": _safe(response.get("response")),
            "parts": _safe(response.get("parts")),
            "will_continue": response.get("will_continue", response.get("willContinue")),
            "scheduling": response.get("scheduling"),
        }
    if kind == "tool_call":
        call = cast(dict[str, Any], _pick(part, "tool_call", "toolCall"))
        return {**descriptor, "args": _safe(call.get("args", {}))}
    if kind == "tool_response":
        response = cast(dict[str, Any], _pick(part, "tool_response", "toolResponse"))
        return {
            **descriptor,
            "response": _safe(response.get("response")),
        }
    if kind == "executable_code":
        code = cast(dict[str, Any], _pick(part, "executable_code", "executableCode"))
        return {
            **descriptor,
            "code": code.get("code"),
        }
    if kind == "code_execution_result":
        result = cast(
            dict[str, Any],
            _pick(part, "code_execution_result", "codeExecutionResult"),
        )
        return {
            **descriptor,
            "output": result.get("output"),
        }
    return descriptor


def _content_signature(role: str, parts: list[dict[str, Any]]) -> str:
    payload = {"role": role, "parts": [_signature_part(part) for part in parts]}
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()
'''
if text.count(old_signature) != 1:
    raise RuntimeError("expected one observable signature anchor")
text = text.replace(old_signature, new_signature, 1)

method_start = text.index(
    '    @model_validator(mode="after")\n'
    "    def records_must_be_ordered_and_consistent("
)
method_end = text.index("\n\ndef _call_index", method_start)
new_method = '''    @model_validator(mode="after")
    def records_must_be_ordered_and_consistent(self) -> GeminiGenerateContentDocument:
        response_ids: set[str] = set()
        content_signatures: dict[str, str] = {}
        call_signatures: dict[str, str] = {}
        previous_timestamp: datetime | None = None
        item_count = 0

        def register_function_calls(parts: list[dict[str, Any]]) -> None:
            for part in parts:
                call = _pick(part, "function_call", "functionCall")
                if not isinstance(call, dict):
                    continue
                call_id = call.get("id")
                name = call.get("name")
                args = call.get("args", {})
                if not isinstance(call_id, str) or not call_id:
                    raise ValueError("function call id must be a non-empty string")
                if not isinstance(name, str) or not name:
                    raise ValueError("function call name must be a non-empty string")
                if not isinstance(args, dict):
                    raise ValueError("function call args must be an object")
                signature = json.dumps(
                    {"name": name, "args": args},
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                )
                existing = call_signatures.get(call_id)
                if existing is not None and existing != signature:
                    raise ValueError(
                        "function call ids must retain identical definitions"
                    )
                call_signatures[call_id] = signature

        def register_content(
            content_id: str,
            role: str,
            parts: list[dict[str, Any]],
            conflict_message: str,
        ) -> None:
            nonlocal item_count
            item_count += len(parts)
            register_function_calls(parts)
            signature = _content_signature(role, parts)
            existing = content_signatures.get(content_id)
            if existing is not None and existing != signature:
                raise ValueError(conflict_message)
            content_signatures[content_id] = signature

        for record in self.records:
            response_id = cast(
                str,
                _pick(record.response, "response_id", "responseId"),
            )
            if response_id in response_ids:
                raise ValueError("response ids must be unique")
            response_ids.add(response_id)
            if previous_timestamp is not None and record.timestamp < previous_timestamp:
                raise ValueError("record timestamps must be nondecreasing")
            previous_timestamp = record.timestamp

            request_contents = list(record.contents)
            if record.system_instruction is not None:
                request_contents.append(record.system_instruction)
            for request_content in request_contents:
                register_content(
                    request_content.id,
                    request_content.role,
                    request_content.parts,
                    "content ids must retain identical observable content",
                )

            for candidate_index, raw_candidate in enumerate(
                record.response["candidates"]
            ):
                candidate = cast(dict[str, Any], raw_candidate)
                candidate_content = candidate.get("content")
                if not isinstance(candidate_content, dict):
                    continue
                candidate_parts = _parts(
                    candidate_content.get("parts"),
                    "candidate.content.parts",
                )
                register_content(
                    record.candidate_ids[candidate_index],
                    "model",
                    candidate_parts,
                    "candidate ids must retain identical observable content",
                )

            if item_count > _MAX_PROVIDER_ITEMS:
                raise ValueError(f"document exceeds {_MAX_PROVIDER_ITEMS} provider items")

        _ensure_json(self.metadata, "document metadata")
        return self
'''
text = text[:method_start] + new_method + text[method_end:]
path.write_text(text, encoding="utf-8")

Path(__file__).unlink()
