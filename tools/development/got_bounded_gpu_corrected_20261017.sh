(
set -Eeuo pipefail
D="$HOME/projects/shadps4/logs"
OLD="got-gow-shreg-table-20261017-205408"
NEW="got-bounded-gpu-corrected-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$D"
tar -xOzf "$D/$OLD.tar.gz" "$OLD.worker.sh" > "$D/$NEW.worker.sh"
python3 - "$D/$NEW.worker.sh" <<'PY'
from pathlib import Path
import hashlib,sys
p=Path(sys.argv[1]); raw=p.read_bytes()
assert hashlib.sha256(raw).hexdigest()=="2eac065fee8bf3046ec408729f1c9eff71abaee5bb2ddbb1e6e91cc34b660d99", "ORIGINAL_WORKER_CHANGED"
s=raw.decode()
fixes={
'e9f470b359845a456139a865aa1d7b3001f0dbfd':'f2509c62cb005ad42ec25247698dc4c3fa8e3e56',
'experiment/got-gow-shreg-and-table-identity-20261017':'fix/got-bounded-gpu-texture-index-20261017',
'e5ea2db3000da81a283da81e94b29f005554978b':'a65198f8cd11889af853871062cddb3ccaa95e3b',
'24c45c206819755e7cc2624df49fdbda82a273c1':'4a3791918648121788f8f1b14df95ebda50e4b45',
'420020e89e1d565433fabda634e54eb7bedcb3b2':'71cbd3f9f87dca933f425534955364f054540a05',
'SHADER_CACHE_VERSION=38':'SHADER_CACHE_VERSION=39',
}
for old,new in fixes.items():
    assert s.count(old)==1, f"UNEXPECTED_WORKER_LAYOUT: {old}"
    s=s.replace(old,new)
expected="665a7b3e6166ee04cff2fca20846ec321fcb41c3126129ed30cbc8161cb073fe"
assert hashlib.sha256(s.encode()).hexdigest()==expected, "GENERATED_WORKER_HASH_MISMATCH"
p.write_text(s)
p.chmod(0o700)
print('CANDIDATE_PATCH=VERIFIED')
print('WORKER_SHA256='+expected)
PY
bash -n "$D/$NEW.worker.sh"
echo "RUN=$NEW"
set +e
bash "$D/$NEW.worker.sh" "$NEW" 2>&1 | tee "$D/$NEW.log"
RC=${PIPESTATUS[0]}
set -e
echo "RESULT=$RC"
echo "REPORT=$D/$NEW.tar.gz"
echo 'SSH_SESSION=UNCHANGED'
)
