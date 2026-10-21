# Media Segmentation

For timed captions, normalize HTML entities/tags, collapse repeated rolling-caption cues, and construct the smallest sentence span containing the target term. Use the supplied sentence and timestamp to disambiguate repeated terms.

The default clip window is the selected span plus at most 0.18 seconds on each side. It must not cross a neighboring sentence boundary. Accept optional lookup fields `clip_start` and `clip_end` for manually verified exceptions. Record the final start/end and duration in the manifest.

`ContextAudio` and video GIF must use the same verified window. `TermAudio` is a separate standard pronunciation of the term or phrase, never a slice of surrounding source audio. When a single-sentence boundary cannot be established, mark the item `needs_alignment` and do not write a media card.
