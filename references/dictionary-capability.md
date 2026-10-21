# Dictionary Capability

Dictionary lookup is a first-class capability. Use the local provider selected by language and preserve UTF-8 end to end.

Automatic providers currently cover English, Chinese, and Japanese only. Other language families must return a missing dictionary rather than fall back to English; identical spellings can have unrelated meanings (French `manger`, English `manger`). An explicitly supplied compatible dictionary file can extend coverage. Missing dictionary data does not block a text card with a verified contextual meaning; keep `dictionary=missing` separate from `contextual_meaning=provided`. Never fill another language's pronunciation or gloss into the reference panel.

Return structured data for every matched item: headword, phonetic, part of speech, English definition, Chinese translation, inflections/exchange data when present, provider name, and lookup status. A supplied, source-grounded contextual definition is the primary answer. Keep broader dictionary translation/definition in a collapsed reference section; do not concatenate all senses into the main answer. Without a contextual meaning the record remains a draft, even when a dictionary entry exists.

For multiword terms, try exact phrase lookup first, then a normalized phrase/headword fallback. If no exact entry exists, keep the phrase as the headword and record the missing exact-entry status; never pretend a component-word definition is the full phrase definition.

The card template may render readable dictionary fields, but must never render raw `ResourceStatus`, filesystem paths, JSON diagnostics, or replacement-character/garbled text. Fail validation when decoded output contains replacement characters or invalid UTF-8.
