# assets/

Release-only files bundled by [`pyside6-deploy`](../pysidedeploy.spec).

## App icon

[`icons/soundtrack.png`](icons/soundtrack.png) is the pixel-art app icon by
[magnific](https://www.flaticon.com/authors/magnific) on
[Flaticon](https://www.flaticon.com/), used under the Flaticon free license
(attribution required). The `.icns` and `.ico` siblings are generated from it;
[`scripts/select_icon.py`](../scripts/select_icon.py) points the deploy spec at
the format of the current platform. See [`icons/README.md`](icons/README.md).

## ffmpeg

[`scripts/fetch_ffmpeg.py`](../scripts/fetch_ffmpeg.py) downloads the pinned
static build into `assets/ffmpeg/` for the requested target, verifies its
SHA-256 and records the source URL in `assets/ffmpeg/SOURCE.txt`. The binary is
git-ignored; a source checkout uses `ffmpeg` from `PATH` instead
([`downloader.find_ffmpeg`](../src/es_ost/downloader.py)).

Only `ffmpeg` is fetched. The source duration needed to size the preview cut is
read from the MP3's own frames with mutagen, so the static `ffprobe` that ships
next to it (~78 MB) is never installed or bundled.

```sh
python scripts/fetch_ffmpeg.py                      # native target
python scripts/fetch_ffmpeg.py --target macos-arm64
```

ffmpeg.org does not publish binaries; these are the static builds linked from
its [download page](https://ffmpeg.org/download.html):

| target | build | license |
|---|---|---|
| linux-x86_64, windows-x64 | [BtbN FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds) `ffmpeg-master-latest-*-lgpl` | LGPL |
| macos-x86_64 | [evermeet.cx](https://evermeet.cx/ffmpeg/) 9.1 | GPL |
| macos-arm64 | [OSXExperts.net](https://www.osxexperts.net/) 9.1 | GPL |

The macOS builds include GPL components, so a release that bundles them must
comply with the GPL as well as the app's AGPL-3.0-or-later. The app only calls
`ffmpeg` as a separate process; the corresponding sources are linked from the
two projects above and from `SOURCE.txt`. Linux and Windows use LGPL builds for
the same reason.

## Credentials

The developer credentials are not stored here. They live in the root `.env`
(git-ignored) and are embedded through
[`resource_data.qrc`](../resource_data.qrc); see the README's packaging
section.
