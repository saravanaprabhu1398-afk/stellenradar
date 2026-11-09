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


def test_invented_salary_is_sent_back_then_fixed():
    invented = {**FIELDS, "salary_min_eur": 100000, "salary_max_eur": 100000}
    client = FakeBedrock([tool_response(invented), tool_response(FIELDS)])
    x = Extractor(client, "model").extract(POSTING)
    assert x.ok
    assert x.attempts == 2
    assert x.fields["salary_min_eur"] is None
    retry = client.calls[1]["messages"][-1]["content"][0]["toolResult"]["content"][0]["text"]
    assert "salary_min_eur=100000 does not appear" in retry


def test_salary_invented_twice_is_nulled_not_dead_lettered():
    invented = {**FIELDS, "salary_min_eur": 100000, "salary_max_eur": None, "seniority": "senior"}
    x = Extractor(FakeBedrock([tool_response(invented), tool_response(invented)]), "model").extract(POSTING)
    assert x.ok
    assert x.fields["salary_min_eur"] is None
    assert x.fields["seniority"] == "senior"
    assert x.problems[0].startswith("salary_min_eur=100000")


def test_stated_salary_is_kept():
    posting = {**POSTING, "description": "Python, AWS. Gehalt 65.000 - 80.000 € p.a."}
    stated = {**FIELDS, "salary_min_eur": 65000, "salary_max_eur": 80000}
    x = Extractor(FakeBedrock([tool_response(stated)]), "model").extract(posting)
    assert x.attempts == 1
    assert (x.fields["salary_min_eur"], x.fields["salary_max_eur"]) == (65000, 80000)


def test_prompt_v3_loads():
    assert "english_only is true ONLY" in Extractor(FakeBedrock([]), "model", "v3").system
