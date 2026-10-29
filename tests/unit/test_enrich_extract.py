from enrich.extract import MAX_DESCRIPTION_CHARS, Extractor

FIELDS = {
    "skills": ["Python"],
    "cloud_platforms": ["aws"],
    "seniority": "mid",
    "salary_min_eur": None,
    "salary_max_eur": None,
    "german_level_required": "none",
    "english_only": True,
    "visa_sponsorship_mentioned": False,
    "remote_policy": "remote",
}
POSTING = {"refnr": "R1", "content_hash": "h1", "title": "Data Engineer", "employer": "ACME", "location": None,
           "description": "Build {pipelines} with Python on AWS."}  # fmt: skip


def tool_response(fields):
    return {
        "output": {"message": {"role": "assistant", "content": [{"toolUse": {"toolUseId": "t1", "name": "x",
                                                                             "input": fields}}]}},
        "usage": {"inputTokens": 100, "outputTokens": 20},
    }  # fmt: skip


def text_response():
    return {"output": {"message": {"role": "assistant", "content": [{"text": "Sure!"}]}}, "usage": {}}


class FakeBedrock:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def converse(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def test_valid_first_answer():
    client = FakeBedrock([tool_response(FIELDS)])
    x = Extractor(client, "model").extract(POSTING)
    assert x.ok
    assert x.attempts == 1
    assert x.fields["skills"] == ["python"]
    assert (x.input_tokens, x.output_tokens) == (100, 20)
    call = client.calls[0]
    assert call["toolConfig"]["toolChoice"] == {"any": {}}
    assert call["inferenceConfig"]["temperature"] == 0
    assert "Build {pipelines}" in call["messages"][0]["content"][0]["text"]


def test_retry_shows_validation_errors_then_succeeds():
    client = FakeBedrock([tool_response({**FIELDS, "seniority": "rockstar"}), tool_response(FIELDS)])
    x = Extractor(client, "model").extract(POSTING)
    assert x.ok
    assert x.attempts == 2
    assert x.input_tokens == 200
    retry = client.calls[1]["messages"][-1]["content"][0]["toolResult"]
    assert retry["status"] == "error"
    assert "seniority" in retry["content"][0]["text"]


def test_invalid_twice_is_a_dead_letter():
    bad = tool_response({**FIELDS, "english_only": "yes"})
    x = Extractor(FakeBedrock([bad, bad]), "model").extract(POSTING)
    assert not x.ok
    assert x.attempts == 2
    assert "english_only" in x.problems[0]
    assert x.last_input["english_only"] == "yes"


def test_no_tool_call_is_retried():
    client = FakeBedrock([text_response(), text_response()])
    x = Extractor(client, "model").extract(POSTING)
    assert not x.ok
    assert x.problems == ["the model did not call the tool"]
    assert "Call the tool" in client.calls[1]["messages"][-1]["content"][0]["text"]


def test_long_description_is_truncated():
    msg = Extractor(FakeBedrock([]), "model").user_message({**POSTING, "description": "x" * 50_000})
    assert msg.count("x") <= MAX_DESCRIPTION_CHARS + 5
