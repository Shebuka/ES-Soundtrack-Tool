# assets/

Release-only files bundled by [`pysidedeploy.spec`](../pysidedeploy.spec).

`ffmpeg` and `ffprobe` are looked up in `assets/ffmpeg/` first and fall back to
`PATH` ([`downloader.find_ffmpeg`](../src/es_ost/downloader.py)), so a source
checkout only needs ffmpeg installed normally. For a release build drop a
static pair built with `libmp3lame`, `libvorbis` and `pcm_s16le` support here:

```
assets/ffmpeg/ffmpeg
assets/ffmpeg/ffprobe
```

The binaries are machine-specific and git-ignored — do not commit them. The
developer credentials are embedded separately through
[`resource_data.qrc`](../resource_data.qrc), not from this folder.
