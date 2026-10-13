"""Static scorer: how faithfully does a generated screen use the SDS design system?

Works on .tsx/.ts/.css files with regexes (no Node needed), so it runs anywhere.
Validated against Figma's own reference screens in sds/src/examples (see tests/).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

SDS_MODULES = {"primitives", "compositions", "layout", "icons", "images", "hooks", "data", "utils"}
FORBIDDEN_PREFIXES = ("react-aria", "@react-aria", "react-stately", "@react-stately",
                      "@mui", "@chakra-ui", "antd", "@radix-ui", "tailwind", "styled-components",
                      "@headlessui", "lucide-react", "react-icons")

# Raw HTML elements that SDS already provides a component for (Button, Input, Link, Text*, Image, ...)
RAW_CONTENT = {"button", "input", "select", "textarea", "a", "img", "label", "p", "h1", "h2", "h3",
               "h4", "h5", "h6", "hr", "table", "dialog", "nav", "svg", "strong", "em", "small"}
# Raw layout elements SDS wants replaced by Flex / FlexItem / Section / Grid
RAW_LAYOUT = {"div", "section", "main", "header", "footer", "article", "aside", "ul", "ol", "li", "span"}

IMPORT_RE = re.compile(r"import\s+(?:type\s+)?(?:(\w+)\s*,?\s*)?(?:\{([^}]*)\})?\s*(?:\*\s+as\s+(\w+))?\s*from\s*[\"']([^\"']+)[\"']", re.S)
JSX_OPEN_RE = re.compile(r"<([A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z0-9]+)?)(?=[\s/>])")
LOCAL_COMP_RE = re.compile(r"(?:function|const|let|class)\s+([A-Z][A-Za-z0-9]*)\b")
HEX_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b")
COLORFN_RE = re.compile(r"\b(?:rgba?|hsla?)\(")
LENGTH_RE = re.compile(r"(?<![\w-])(?!0(?:px|rem|em)\b)\d*\.?\d+(?:px|rem|em)\b")
FONT_RE = re.compile(r"font-?[fF]amily\s*:\s*[\"']?(?!var\()[A-Za-z]")
VAR_RE = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)")
STYLE_ATTR_RE = re.compile(r"style=\{\{")
CLASSNAME_RE = re.compile(r"className=")


def defined_tokens(sds_root: Path) -> set[str]:
    names: set[str] = set()
    for css in (sds_root / "src").rglob("*.css"):
        names |= set(re.findall(r"(--sds-[A-Za-z0-9_-]+)\s*:", css.read_text(encoding="utf-8", errors="ignore")))
    return names


def exported_names(sds_root: Path) -> set[str]:
    names: set[str] = set()
    for f in list((sds_root / "src" / "ui").rglob("*.ts*")) + list((sds_root / "src" / "data").rglob("*.ts*")):
        t = f.read_text(encoding="utf-8", errors="ignore")
        names |= set(re.findall(r"export\s+(?:declare\s+)?(?:const|function|class|type|interface|enum)\s+([A-Za-z0-9_]+)", t))
        for grp in re.findall(r"export\s*\{([^}]*)\}", t):
            for part in grp.split(","):
                part = part.strip()
                if part:
                    names.add(part.split(" as ")[-1].strip())
    return names


@dataclass
class ScreenScore:
    file: str
    ds_elements: int = 0            # JSX elements that are SDS components
    raw_content: int = 0            # <button>, <p>, <h2>, <img>... where SDS has a component
    raw_layout: int = 0             # <div>, <section>, <span>...
    local_components: int = 0       # new one-off components defined in the screen file
    other_elements: int = 0         # anything else (fragments, unknown imports)
    hallucinated_imports: list[str] = field(default_factory=list)  # names imported from SDS that SDS does not export
    forbidden_imports: list[str] = field(default_factory=list)
    sds_vars: int = 0               # var(--sds-...) that exist
    invented_vars: list[str] = field(default_factory=list)  # var(--sds-...) that do not exist
    hardcoded_values: int = 0       # hex, rgb(), px/rem/em, literal font-family
    style_attrs: int = 0
    classnames: int = 0
    ds_components_used: list[str] = field(default_factory=list)

    @property
    def component_reuse(self) -> float:
        d = self.ds_elements + self.raw_content + self.local_components
        return self.ds_elements / d if d else 0.0

    @property
    def layout_fidelity(self) -> float:
        # share of layout done with SDS layout components rather than raw divs
        lay_ds = sum(1 for c in self.ds_components_used if c in {"Flex", "FlexItem", "Section", "Grid", "GridItem"})
        d = lay_ds + self.raw_layout
        return lay_ds / d if d else 1.0

    @property
    def token_fidelity(self) -> float:
        d = self.sds_vars + self.hardcoded_values + len(self.invented_vars)
        return self.sds_vars / d if d else 1.0

    def row(self) -> dict:
        r = asdict(self)
        r["hallucinated_imports"] = ";".join(self.hallucinated_imports)
        r["forbidden_imports"] = ";".join(self.forbidden_imports)
        r["invented_vars"] = ";".join(self.invented_vars)
        r["ds_components_used"] = ";".join(sorted(set(self.ds_components_used)))
        r["component_reuse"] = round(self.component_reuse, 3)
        r["layout_fidelity"] = round(self.layout_fidelity, 3)
        r["token_fidelity"] = round(self.token_fidelity, 3)
        return r


def strip_comments(t: str) -> str:
    t = re.sub(r"/\*.*?\*/", "", t, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", t)


def score_values(text: str, tokens: set[str], s: ScreenScore) -> None:
    for v in VAR_RE.findall(text):
        if v.startswith("--sds-"):
            if v in tokens:
                s.sds_vars += 1
            else:
                s.invented_vars.append(v)
    no_urls = re.sub(r"https?://\S+", "", text)
    s.hardcoded_values += len(HEX_RE.findall(no_urls)) + len(COLORFN_RE.findall(no_urls))
    s.hardcoded_values += len(LENGTH_RE.findall(no_urls)) + len(FONT_RE.findall(no_urls))


def score_tsx(path: Path, tokens: set[str], exports: set[str], extra_css: list[Path] | None = None) -> ScreenScore:
    text = strip_comments(path.read_text(encoding="utf-8", errors="ignore"))
    s = ScreenScore(file=path.name)
    imported_ds: set[str] = set()
    imported_other: set[str] = set()
    for default, named, star, mod in IMPORT_RE.findall(text):
        names = [n.strip().split(" as ")[-1].strip() for n in named.split(",") if n.strip()]
        names = [n.replace("type ", "").strip() for n in names]
        if default:
            names.append(default)
        if star:
            names.append(star)
        if mod.startswith(FORBIDDEN_PREFIXES):
            s.forbidden_imports.append(mod)
        root = mod.split("/")[0]
        if root in SDS_MODULES or "/ui/" in mod or mod.startswith(("../ui", "./ui", "src/ui")):
            for n in names:
                if n not in exports:
                    s.hallucinated_imports.append(n)
                imported_ds.add(n)
        else:
            imported_other |= set(names)
    local = set(LOCAL_COMP_RE.findall(text)) - imported_ds
    screen_name = path.stem
    local.discard(screen_name)
    s.local_components = len(local)
    # ignore generic type arguments like useState<string>
    for tag in JSX_OPEN_RE.findall(re.sub(r"\b\w+<[^>()]*>\(", "(", text)):
        base = tag.split(".")[0]
        if base in imported_ds:
            s.ds_elements += 1
            s.ds_components_used.append(base)
        elif tag in RAW_CONTENT:
            s.raw_content += 1
        elif tag in RAW_LAYOUT:
            s.raw_layout += 1
        elif base in local:
            pass  # counted once as a one-off component definition
        else:
            s.other_elements += 1
    s.style_attrs = len(STYLE_ATTR_RE.findall(text))
    s.classnames = len(CLASSNAME_RE.findall(text))
    score_values(text, tokens, s)
    for css in extra_css or []:
        score_values(strip_comments(css.read_text(encoding="utf-8", errors="ignore")), tokens, s)
    return s


def reference_overlap(gen: ScreenScore, ref: ScreenScore) -> tuple[float, float]:
    g, r = set(gen.ds_components_used), set(ref.ds_components_used)
    recall = len(g & r) / len(r) if r else 0.0
    precision = len(g & r) / len(g) if g else 0.0
    return round(recall, 3), round(precision, 3)
