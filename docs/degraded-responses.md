# Degraded responses

A target can tell `safelabs-eval` that a reply is a fallback, not a real model answer. Such a reply is retried and, if it stays degraded, recorded as `MISSING` instead of being scored.

## The problem

Many apps catch a model failure and answer anyway: an HTTP 200 with a keyword search, a canned message or a cached result. `safelabs-eval` sees a normal reply and scores it. In the ASI01 pilot ([Discussion #68](https://github.com/AgentSafeLabs/safelabs-eval/discussions/68)), ASI01-018 and ASI01-011 were fallback keyword searches after provider 429s, and both were scored `UNCERTAIN`. A fallback says nothing about how the model behaves, so it should not count as a result.

## Signalling from an HTTP target

`HttpAdapter` treats a response as degraded when either of these holds:

- the response header `X-SafeLabs-Degraded` is `1`, `true` or `yes` (any case);
- the JSON body is an object with a top-level `"safelabs_degraded": true`.

Minimal example:

```http
HTTP/1.1 200 OK
Content-Type: application/json
X-SafeLabs-Degraded: 1

{"response": "keyword search for: ...", "safelabs_degraded": true}
```

Either signal is enough. Both are read only on a successful (below 400) response; an HTTP error is already treated as an error.

## Signalling from a framework adapter

Set `degraded=True` on the `AgentResponse` your adapter returns. The field defaults to `False`, so existing adapters are unchanged.

```python
return AgentResponse(output=fallback_text, degraded=True)
```

## What safelabs-eval does

A degraded response is handled like an infrastructure error:

1. It is retried according to the retry profile (`--retry-profile`, `--max-attempts`).
2. If a retry returns a normal answer, that answer is scored as usual.
3. If the last attempt is still degraded, the trial is recorded as `MISSING` with `error_class` `infrastructure` and `error_subclass` `degraded`. It is excluded from every count.

A degraded response is never passed to the scorer. With `--max-attempts 1` the first degraded response is `MISSING` at once. A `MISSING` row has the same keys as any other infrastructure `MISSING` row.

## Bridge example

If your app can tell that a model call failed, set the header when it falls back:

```python
try:
    answer = call_model(question)
    return JSONResponse({"response": answer})
except ModelError:
    answer = keyword_search(question)
    return JSONResponse({"response": answer}, headers={"X-SafeLabs-Degraded": "1"})
```

Only your test or staging environment needs this. Production behaviour for users can stay the same.
