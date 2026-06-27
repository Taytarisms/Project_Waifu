import glob, os, re

paths = []
# Check pip package
paths += glob.glob(os.path.join(".venv", "Lib", "site-packages", "**", "*.py"), recursive=True)
# Check HF cache
cache = os.path.join(os.path.expanduser("~"), ".cache", "huggingface", "hub")
if os.path.isdir(cache):
    paths += glob.glob(os.path.join(cache, "**", "*.py"), recursive=True)

patched = 0
for p in paths:
    try:
        text = open(p, encoding="utf-8").read()
        if "@check_model_inputs()" in text:
            text = text.replace("@check_model_inputs()", "@check_model_inputs")
            open(p, "w", encoding="utf-8").write(text)
            print(f"  patched: {p}")
            patched += 1
    except Exception:
        pass
print(f"\nDone — patched {patched} file(s).")