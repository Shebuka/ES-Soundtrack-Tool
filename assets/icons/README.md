# App icon

`soundtrack.png` is the pixel-art soundtrack icon by
[magnific](https://www.flaticon.com/authors/magnific) on
[Flaticon](https://www.flaticon.com/), used under the Flaticon free license
(attribution required).

`soundtrack.icns` and `soundtrack.ico` are exports of the 512×512 PNG for
macOS and Windows (`iconutil`/`sips`); Linux uses the PNG. Run
[`scripts/select_icon.py`](../../scripts/select_icon.py) before a local build
so `pysidedeploy.spec` points at the format this platform needs; the release
workflow does it for every target.
