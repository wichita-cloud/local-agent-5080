#!/usr/bin/env python3
"""mtp-graft.py <base.gguf> <donor-url> <out.gguf>: add a model's MTP layer to a GGUF that lacks it.

MTP (multi-token prediction) lets llama.cpp draft several tokens per step and check them in one pass, which is
where the MTP profile's speed comes from. The official Qwen3.5-27B has an MTP layer, but the IQ4_XS file this stack
uses (an imatrix quant) was converted without it, and the quants that do include it are ~1 GB larger, which does not
fit at 32k context on 16 GB. So this copies just the MTP layer (one extra block, ~250 MB) out of an MTP-enabled
quant of the SAME model into a new file next to yours:

  * only the donor's header (~16 MB) and its MTP block are downloaded (HTTP range requests), not the whole 15 GB
  * your base file is opened read-only and never modified; the result is a new file
  * every non-MTP tensor must match the donor by name and shape, or nothing is written (wrong model = refused)

Called by `bin/model.sh download <profile>` for profiles that set MTP_DONOR_URL. Needs the `gguf` Python package.
"""
import os
import sys
import urllib.request

from gguf import GGUFReader, GGUFValueType, GGUFWriter

base_p, donor_url, out_p = sys.argv[1:4]
work = out_p + ".donor-part"


def fetch(start, end, dest, size):
    """Write bytes [start, end] of the donor into a sparse local file of the donor's full size."""
    req = urllib.request.Request(donor_url, headers={"Range": f"bytes={start}-{end}"})
    with urllib.request.urlopen(req, timeout=120) as r, open(dest, "r+b" if os.path.exists(dest) else "w+b") as f:
        if r.status != 206:
            sys.exit(f"the server ignored the range request (HTTP {r.status}); refusing to download the whole file")
        f.truncate(size)
        f.seek(start)
        got = 0
        while chunk := r.read(4 << 20):
            f.write(chunk)
            got += len(chunk)
    if got != end - start + 1:
        sys.exit(f"short read from the donor: {got} of {end - start + 1} bytes. Run it again")


with urllib.request.urlopen(urllib.request.Request(donor_url, method="HEAD"), timeout=60) as r:
    size = int(r.headers["Content-Length"])
donor = None
for head in (16 << 20, 64 << 20):              # metadata (tokenizer included) is ~11 MB on this model
    if os.path.exists(work):
        os.remove(work)
    fetch(0, head - 1, work, size)
    try:
        donor = GGUFReader(work, "r")
        break
    except Exception:  # noqa: BLE001  (header longer than what we fetched: try a bigger slice)
        donor = None
if donor is None:
    sys.exit("could not read the donor's header")

base = GGUFReader(base_p, "r")
arch = base.fields["general.architecture"].contents()
if donor.fields["general.architecture"].contents() != arch:
    sys.exit(f"donor architecture {donor.fields['general.architecture'].contents()} != base {arch}")
n_base = base.fields[f"{arch}.block_count"].contents()
nextn = f"{arch}.nextn_predict_layers"
if nextn in base.fields:
    sys.exit("the base file already has an MTP layer; use it directly")
if nextn not in donor.fields or donor.fields[f"{arch}.block_count"].contents() != n_base + 1:
    sys.exit("the donor is not an MTP build of this model (expected exactly one extra block)")
dshape = {t.name: list(map(int, t.shape)) for t in donor.tensors}
for t in base.tensors:
    if dshape.get(t.name) != list(map(int, t.shape)):
        sys.exit(f"tensor {t.name} differs between base and donor: they are not the same model")

prefix = f"blk.{n_base}."
mtp = [t for t in donor.tensors if t.name.startswith(prefix)]
lo = min(t.data_offset for t in mtp)
hi = max(t.data_offset + t.n_bytes for t in mtp)
print(f"downloading the MTP layer: {len(mtp)} tensors, {(hi - lo) >> 20} MiB", flush=True)
fetch(lo, hi - 1, work, size)
donor = GGUFReader(work, "r")
mtp = [t for t in donor.tensors if t.name.startswith(prefix)]

w = GGUFWriter(out_p + ".tmp", arch=arch)
for f in base.fields.values():
    if f.name == "general.architecture" or f.name.startswith("GGUF."):
        continue
    vt = f.types[0]
    w.add_key_value(f.name, n_base + 1 if f.name == f"{arch}.block_count" else f.contents(), vt,
                    sub_type=f.types[-1] if vt == GGUFValueType.ARRAY else None)
w.add_key_value(nextn, 1, GGUFValueType.UINT32)
tensors = list(base.tensors) + mtp
for t in tensors:
    w.add_tensor_info(t.name, t.data.shape, t.data.dtype, t.data.nbytes, t.tensor_type)
w.write_header_to_file()
w.write_kv_data_to_file()
w.write_ti_data_to_file()
for i, t in enumerate(tensors):
    w.write_tensor_data(t.data)
    if i % 200 == 0:
        print(f"  writing {i}/{len(tensors)} tensors", flush=True)
w.close()
GGUFReader(out_p + ".tmp", "r")                # must parse back before it replaces anything
os.replace(out_p + ".tmp", out_p)
os.remove(work)
print(f"wrote {out_p}")
