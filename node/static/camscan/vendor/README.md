# Vendored: ZXing (TypeScript port)

Used by `../camscan.js` as the barcode decoder when the browser has no
suitable `BarcodeDetector` (for example iPhone Safari). Vendored so nodes
work offline on the LAN with no CDN.

| | |
|---|---|
| Package | [`@zxing/library`](https://www.npmjs.com/package/@zxing/library) |
| Version | 0.21.3 |
| File | `umd/index.min.js` from the npm tarball, renamed `zxing-library-0.21.3.min.js` |
| Tarball sha1 (matches npm registry `dist.shasum`) | `0a4cb777701884131832b05922d7e88ef1b87ab4` |
| File sha256 | `d7cc8f69dd70bdcf3ac00c9ae572bf2acb9f4132ba379c72df842e4db918652d` |
| License | Apache-2.0 (see `LICENSE-zxing`) |

To update: download the new tarball from the npm registry, check its sha1
against `dist.shasum`, copy `package/umd/index.min.js` here with the new
version in the file name, update `ZXING_SRC` in `camscan.js` and this table.
