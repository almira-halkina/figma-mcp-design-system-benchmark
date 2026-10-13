"""Scorer sanity checks. Run: python -m pytest tests  (needs `python bench.py setup` first)."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import score as S

SDS = Path(__file__).resolve().parents[1] / "work" / "sds"
T, E = S.defined_tokens(SDS), S.exported_names(SDS)


def write(tmp_path, body):
    p = tmp_path / "Screen.tsx"; p.write_text(body, encoding="utf-8"); return S.score_tsx(p, T, E)


def test_reference_screens_score_high():
    for f in (SDS / "src" / "examples").glob("*.tsx"):
        if f.stem == "Demo":
            continue
        s = S.score_tsx(f, T, E)
        assert s.component_reuse >= 0.8, f.name
        assert s.hardcoded_values == 0 and not s.hallucinated_imports, f.name


def test_detects_raw_html_hardcoded_values_and_invented_tokens(tmp_path):
    s = write(tmp_path, 'import { Button } from "primitives";\n'
              'function Promo(){return <div/>}\n'
              'export function Screen(){return <div style={{color:"#ff0000",padding:"12px",'
              'gap:"var(--sds-size-space-200)",margin:"var(--sds-not-real)"}}><button>Buy</button><Button>Ok</Button><Promo/></div>}')
    assert s.ds_elements == 1 and s.raw_content == 1 and s.local_components == 1
    assert s.hardcoded_values == 2
    assert s.sds_vars == 1 and s.invented_vars == ["--sds-not-real"]


def test_detects_hallucinated_and_forbidden_imports(tmp_path):
    s = write(tmp_path, 'import { Buttonz } from "primitives";\nimport { Button } from "react-aria-components";\n'
              'export function Screen(){return <Buttonz/>}')
    assert s.hallucinated_imports == ["Buttonz"]
    assert s.forbidden_imports == ["react-aria-components"]
