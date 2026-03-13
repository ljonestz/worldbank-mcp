"""
World Bank Documents MCP Server

Provides tools to search and retrieve World Bank project documents
(PADs, ISRs, Aide Mémoires, PIDs, PCNs, Restructuring Papers, etc.)
from the World Bank's public Open Data APIs.

No authentication required — all documents are publicly accessible.
"""

import json
import base64
import httpx
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict, field_validator
from mcp.server.fastmcp import FastMCP

# ─── Server ───────────────────────────────────────────────────────────────────

mcp = FastMCP("worldbank_mcp")

# ─── Constants ────────────────────────────────────────────────────────────────

WB_DOCS_API    = "https://search.worldbank.org/api/v2/wds"
WB_PROJECTS_API = "https://search.worldbank.org/api/v2/projects"
WB_BASE_URL    = "https://documents.worldbank.org"

HTTP_TIMEOUT = 30.0

# Canonical document type strings accepted by the WB API
DOCUMENT_TYPES = [
    "Project Appraisal Document",
    "Implementation Status and Results Report",
    "Aide Memoire",
    "Project Information Document",
    "Project Concept Note",
    "Restructuring Paper",
    "Additional Financing",
    "Program Document",
    "Project Paper",
    "Technical Assistance Report",
    "Environmental Assessment",
    "Integrated Safeguards Data Sheet",
    "Inspection Panel Report",
    "Country Assistance Strategy",
    "Country Partnership Framework",
    "Systematic Country Diagnostic",
]

# ─── Shared HTTP client ────────────────────────────────────────────────────────

def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
        headers={"User-Agent": "WorldBank-MCP/1.0"},
    )


# ─── Shared helpers ───────────────────────────────────────────────────────────

def _handle_api_error(e: Exception) -> str:
    """Produce actionable error messages from HTTP or network failures."""
    if isinstance(e, httpx.HTTPStatusError):
        code = e.response.status_code
        if code == 404:
            return "Error: Resource not found. Check that the project ID or URL is correct."
        if code == 429:
            return "Error: Rate limit exceeded. Wait a moment before retrying."
        if code >= 500:
            return f"Error: World Bank API server error ({code}). Try again later."
        return f"Error: API request failed with HTTP {code}."
    if isinstance(e, httpx.TimeoutException):
        return "Error: Request timed out. The World Bank API may be slow — try again."
    if isinstance(e, httpx.ConnectError):
        return "Error: Could not connect to the World Bank API. Check your internet connection."
    return f"Error: Unexpected error — {type(e).__name__}: {e}"


def _extract_doc_fields(doc: dict) -> dict:
    """Normalise a raw WB document record into a clean, consistent dict."""
    pdf_url = doc.get("pdfurl") or doc.get("url") or ""
    # Ensure absolute URLs
    if pdf_url and pdf_url.startswith("/"):
        pdf_url = WB_BASE_URL + pdf_url

    return {
        "id":          doc.get("id", ""),
        "title":       doc.get("display_title") or doc.get("doctit") or "Untitled",
        "doc_type":    doc.get("docty", "Unknown"),
        "date":        doc.get("docdt", ""),
        "project_id":  doc.get("projectid", ""),
        "country":     doc.get("countryname", ""),
        "language":    doc.get("lang_exact", ""),
        "abstract":    (doc.get("abstracts") or "")[:400],
        "pdf_url":     pdf_url,
        "disclosure":  doc.get("disclosure_type", ""),
    }


def _format_doc_markdown(d: dict, idx: int) -> str:
    """Render a single document record as a Markdown list item."""
    lines = [
        f"**{idx}. {d['title']}**",
        f"- Type: {d['doc_type']}",
        f"- Date: {d['date'] or 'Unknown'}",
        f"- Country: {d['country'] or 'Unknown'}",
        f"- Project ID: {d['project_id'] or 'N/A'}",
        f"- Language: {d['language'] or 'Unknown'}",
        f"- PDF: {d['pdf_url'] or 'Not available'}",
    ]
    if d["abstract"]:
        lines.append(f"- Abstract: {d['abstract']}...")
    return "\n".join(lines)


def _format_project_markdown(p: dict, idx: int) -> str:
    lines = [
        f"**{idx}. {p.get('project_name', 'Unnamed')}**",
        f"- ID: {p.get('id', 'N/A')}",
        f"- Country: {p.get('countryname', 'Unknown')}",
        f"- Status: {p.get('status', 'Unknown')}",
        f"- Sector: {p.get('sector1', {}).get('Name', 'Unknown') if isinstance(p.get('sector1'), dict) else p.get('sector1', 'Unknown')}",
        f"- Approval Date: {p.get('boardapprovaldate', 'Unknown')}",
        f"- Closing Date: {p.get('closingdate', 'Unknown')}",
        f"- Lending: {p.get('lendinginstr', 'Unknown')}",
    ]
    if p.get("project_abstract"):
        abstract = str(p["project_abstract"])[:400]
        lines.append(f"- Abstract: {abstract}...")
    return "\n".join(lines)


# ─── Input models ─────────────────────────────────────────────────────────────

class SearchDocumentsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: Optional[str] = Field(
        default=None,
        description="Free-text search query (title, abstract keywords). E.g. 'fragility Guinea health'",
        max_length=300,
    )
    country_code: Optional[str] = Field(
        default=None,
        description="ISO2 country code. E.g. 'GH' (Ghana), 'SS' (South Sudan), 'ML' (Mali)",
        max_length=2,
        min_length=2,
    )
    project_id: Optional[str] = Field(
        default=None,
        description="World Bank project ID. E.g. 'P123456'. Narrows results to a single project.",
        max_length=20,
    )
    doc_types: Optional[List[str]] = Field(
        default=None,
        description=(
            "List of document type strings. Valid values: "
            "'Project Appraisal Document', 'Implementation Status and Results Report', "
            "'Aide Memoire', 'Project Information Document', 'Project Concept Note', "
            "'Restructuring Paper', 'Additional Financing'. "
            "Omit to return all types."
        ),
        max_length=10,
    )
    start_year: Optional[int] = Field(
        default=None,
        description="Filter documents published from this year onwards. E.g. 2018",
        ge=1970,
        le=2030,
    )
    end_year: Optional[int] = Field(
        default=None,
        description="Filter documents published up to and including this year. E.g. 2024",
        ge=1970,
        le=2030,
    )
    limit: int = Field(
        default=10,
        description="Maximum number of results to return (1–50).",
        ge=1,
        le=50,
    )
    offset: int = Field(
        default=0,
        description="Offset for pagination. Use next_offset from a previous response.",
        ge=0,
    )
    response_format: str = Field(
        default="markdown",
        description="Output format: 'markdown' (human-readable) or 'json' (machine-readable).",
    )

    @field_validator("country_code")
    @classmethod
    def upper_country_code(cls, v: Optional[str]) -> Optional[str]:
        return v.upper() if v else v

    @field_validator("response_format")
    @classmethod
    def validate_format(cls, v: str) -> str:
        if v not in ("markdown", "json"):
            raise ValueError("response_format must be 'markdown' or 'json'")
        return v


class SearchProjectsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: Optional[str] = Field(
        default=None,
        description="Free-text search across project name and abstract. E.g. 'health fragile states'",
        max_length=300,
    )
    country_code: Optional[str] = Field(
        default=None,
        description="ISO2 country code. E.g. 'SS' (South Sudan), 'CD' (DRC)",
        max_length=2,
        min_length=2,
    )
    status: Optional[str] = Field(
        default=None,
        description="Project status filter: 'Active', 'Closed', or 'Pipeline'",
    )
    sector: Optional[str] = Field(
        default=None,
        description="Sector keyword. E.g. 'Health', 'Education', 'Agriculture', 'Social Protection'",
        max_length=100,
    )
    limit: int = Field(default=10, description="Maximum results to return (1–50).", ge=1, le=50)
    offset: int = Field(default=0, description="Pagination offset.", ge=0)
    response_format: str = Field(
        default="markdown",
        description="'markdown' or 'json'",
    )

    @field_validator("country_code")
    @classmethod
    def upper_country_code(cls, v: Optional[str]) -> Optional[str]:
        return v.upper() if v else v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v and v not in ("Active", "Closed", "Pipeline"):
            raise ValueError("status must be 'Active', 'Closed', or 'Pipeline'")
        return v

    @field_validator("response_format")
    @classmethod
    def validate_format(cls, v: str) -> str:
        if v not in ("markdown", "json"):
            raise ValueError("response_format must be 'markdown' or 'json'")
        return v


class GetProjectDocumentsInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    project_id: str = Field(
        ...,
        description="World Bank project ID. E.g. 'P123456'",
        min_length=3,
        max_length=20,
    )
    doc_types: Optional[List[str]] = Field(
        default=None,
        description=(
            "Filter to specific document types. E.g. ['Project Appraisal Document', "
            "'Implementation Status and Results Report']. Omit for all types."
        ),
    )
    response_format: str = Field(default="markdown", description="'markdown' or 'json'")

    @field_validator("response_format")
    @classmethod
    def validate_format(cls, v: str) -> str:
        if v not in ("markdown", "json"):
            raise ValueError("response_format must be 'markdown' or 'json'")
        return v


class FetchDocumentInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    pdf_url: str = Field(
        ...,
        description=(
            "Direct URL to the World Bank document PDF. "
            "Obtain from pdf_url field in search results. "
            "E.g. 'https://documents.worldbank.org/en/publication/documents-reports/documentdetail/...'"
        ),
        min_length=10,
    )
    max_chars: int = Field(
        default=50000,
        description=(
            "Maximum characters to return from the extracted text. "
            "Use lower values (e.g. 20000) for faster responses. "
            "Max 200000."
        ),
        ge=1000,
        le=200000,
    )

    @field_validator("pdf_url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("pdf_url must be a valid http/https URL")
        return v


class ListDocumentTypesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    response_format: str = Field(default="markdown", description="'markdown' or 'json'")


# ─── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool(
    name="wb_search_documents",
    annotations={
        "title": "Search World Bank Documents",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def wb_search_documents(params: SearchDocumentsInput) -> str:
    """Search the World Bank Documents & Reports API for project documents.

    Returns PADs, ISRs, Aide Mémoires, PIDs, Concept Notes, Restructuring Papers,
    and other World Bank project documents. All documents are publicly accessible
    with no authentication required.

    Use this tool to:
    - Find documents for a specific country or project
    - Search by document type (e.g. only PADs, only ISRs)
    - Filter by date range
    - Locate PDF download URLs for further processing

    Args:
        params (SearchDocumentsInput): Search parameters including query, country_code,
            project_id, doc_types, start_year, end_year, limit, offset, response_format.

    Returns:
        str: Formatted list of matching documents with titles, types, dates, countries,
             project IDs, and PDF URLs.

    Schema:
        {
          "total": int,
          "count": int,
          "offset": int,
          "has_more": bool,
          "next_offset": int | null,
          "documents": [
            {
              "id": str,
              "title": str,
              "doc_type": str,
              "date": str,
              "project_id": str,
              "country": str,
              "language": str,
              "abstract": str,
              "pdf_url": str,
              "disclosure": str
            }
          ]
        }
    """
    api_params: dict = {
        "format": "json",
        "rows": params.limit,
        "os": params.offset,
        "fl": "id,display_title,doctit,docty,docdt,projectid,countryname,lang_exact,abstracts,pdfurl,url,disclosure_type",
    }

    if params.query:
        api_params["qterm"] = params.query
    if params.country_code:
        api_params["countrycode"] = params.country_code
    if params.project_id:
        api_params["projectid"] = params.project_id
    if params.doc_types:
        api_params["docty"] = "|".join(params.doc_types)
    if params.start_year:
        api_params["strdate"] = f"{params.start_year}-01-01"
    if params.end_year:
        api_params["enddate"] = f"{params.end_year}-12-31"

    try:
        async with _make_client() as client:
            resp = await client.get(WB_DOCS_API, params=api_params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        return _handle_api_error(e)

    raw_docs = data.get("documents", {})
    total = int(data.get("total", {}).get("value", 0) if isinstance(data.get("total"), dict) else data.get("total", 0))

    docs = [
        _extract_doc_fields(v)
        for k, v in raw_docs.items()
        if k != "facets" and isinstance(v, dict)
    ]

    has_more = total > params.offset + len(docs)
    next_offset = params.offset + len(docs) if has_more else None

    if params.response_format == "json":
        return json.dumps({
            "total": total,
            "count": len(docs),
            "offset": params.offset,
            "has_more": has_more,
            "next_offset": next_offset,
            "documents": docs,
        }, indent=2)

    if not docs:
        return "No documents found matching your search criteria. Try broadening the query or removing filters."

    lines = [
        f"## World Bank Documents — {len(docs)} of {total} results\n",
        *(f"{_format_doc_markdown(d, i+1)}\n" for i, d in enumerate(docs)),
    ]
    if has_more:
        lines.append(f"*More results available. Use offset={next_offset} to fetch the next page.*")
    return "\n".join(lines)


@mcp.tool(
    name="wb_search_projects",
    annotations={
        "title": "Search World Bank Projects",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def wb_search_projects(params: SearchProjectsInput) -> str:
    """Search the World Bank Projects API for active, closed, or pipeline projects.

    Use this tool to:
    - Find World Bank projects by country, sector, or keyword
    - Get project IDs (e.g. P123456) to use in wb_get_project_documents
    - Check project status (Active/Closed/Pipeline) and key dates

    Args:
        params (SearchProjectsInput): Search parameters including query, country_code,
            status, sector, limit, offset, response_format.

    Returns:
        str: Formatted list of matching projects with names, IDs, countries, sectors,
             approval/closing dates, and lending instruments.

    Schema:
        {
          "total": int,
          "count": int,
          "offset": int,
          "has_more": bool,
          "next_offset": int | null,
          "projects": [
            {
              "id": str,
              "project_name": str,
              "countryname": str,
              "status": str,
              "sector1": str,
              "boardapprovaldate": str,
              "closingdate": str,
              "lendinginstr": str,
              "project_abstract": str
            }
          ]
        }
    """
    api_params: dict = {
        "format": "json",
        "rows": params.limit,
        "os": params.offset,
        "fl": "id,project_name,countryname,countrycode,status,sector1,boardapprovaldate,closingdate,lendinginstr,project_abstract",
        "source": "IBRD",
    }

    if params.query:
        api_params["qterm"] = params.query
    if params.country_code:
        api_params["countrycode"] = params.country_code
    if params.status:
        api_params["status_exact"] = params.status
    if params.sector:
        api_params["mjsector_exact"] = params.sector

    try:
        async with _make_client() as client:
            resp = await client.get(WB_PROJECTS_API, params=api_params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        return _handle_api_error(e)

    projects = data.get("projects", {})
    total = int(data.get("total", {}).get("value", 0) if isinstance(data.get("total"), dict) else data.get("total", 0))

    proj_list = [v for k, v in projects.items() if k != "facets" and isinstance(v, dict)]
    has_more = total > params.offset + len(proj_list)
    next_offset = params.offset + len(proj_list) if has_more else None

    if params.response_format == "json":
        return json.dumps({
            "total": total,
            "count": len(proj_list),
            "offset": params.offset,
            "has_more": has_more,
            "next_offset": next_offset,
            "projects": proj_list,
        }, indent=2)

    if not proj_list:
        return "No projects found. Try adjusting filters — status must be exactly 'Active', 'Closed', or 'Pipeline'."

    lines = [
        f"## World Bank Projects — {len(proj_list)} of {total} results\n",
        *(f"{_format_project_markdown(p, i+1)}\n" for i, p in enumerate(proj_list)),
    ]
    if has_more:
        lines.append(f"*More results available. Use offset={next_offset} to fetch the next page.*")
    return "\n".join(lines)


@mcp.tool(
    name="wb_get_project_documents",
    annotations={
        "title": "Get All Documents for a World Bank Project",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def wb_get_project_documents(params: GetProjectDocumentsInput) -> str:
    """Retrieve all publicly available documents for a specific World Bank project ID.

    Use this tool when you already know a project ID (e.g. P123456) and want to:
    - See all document types available (PAD, ISR, Aide Mémoire, etc.)
    - Find PDF URLs for each document
    - Understand the project's documentation history

    Args:
        params (GetProjectDocumentsInput): project_id (required), optional doc_types filter,
            and response_format.

    Returns:
        str: All documents grouped by type, with dates and PDF download URLs.

    Schema:
        {
          "project_id": str,
          "total_documents": int,
          "documents_by_type": {
            "<doc_type>": [
              {
                "id": str, "title": str, "doc_type": str, "date": str,
                "country": str, "pdf_url": str, "abstract": str
              }
            ]
          }
        }
    """
    api_params: dict = {
        "format": "json",
        "rows": 50,
        "os": 0,
        "projectid": params.project_id,
        "fl": "id,display_title,doctit,docty,docdt,projectid,countryname,lang_exact,abstracts,pdfurl,url",
    }
    if params.doc_types:
        api_params["docty"] = "|".join(params.doc_types)

    try:
        async with _make_client() as client:
            resp = await client.get(WB_DOCS_API, params=api_params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as e:
        return _handle_api_error(e)

    raw_docs = data.get("documents", {})
    docs = [
        _extract_doc_fields(v)
        for k, v in raw_docs.items()
        if k != "facets" and isinstance(v, dict)
    ]

    if not docs:
        return (
            f"No documents found for project {params.project_id}. "
            "Check the project ID is correct (format: P followed by 6 digits). "
            "Some project documents may not be publicly disclosed."
        )

    # Group by document type
    by_type: dict = {}
    for d in docs:
        by_type.setdefault(d["doc_type"], []).append(d)

    if params.response_format == "json":
        return json.dumps({
            "project_id": params.project_id,
            "total_documents": len(docs),
            "documents_by_type": by_type,
        }, indent=2)

    lines = [f"## Documents for Project {params.project_id} — {len(docs)} total\n"]
    for doc_type, type_docs in sorted(by_type.items()):
        lines.append(f"### {doc_type} ({len(type_docs)})")
        for i, d in enumerate(type_docs, 1):
            lines.append(f"  **{i}. {d['title']}**")
            lines.append(f"  - Date: {d['date'] or 'Unknown'}")
            lines.append(f"  - PDF: {d['pdf_url'] or 'Not publicly available'}")
            if d["abstract"]:
                lines.append(f"  - Abstract: {d['abstract']}...")
        lines.append("")
    return "\n".join(lines)


@mcp.tool(
    name="wb_fetch_document_text",
    annotations={
        "title": "Fetch and Extract Text from a World Bank PDF",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
async def wb_fetch_document_text(params: FetchDocumentInput) -> str:
    """Download a World Bank PDF document and extract its text content.

    Use this tool to:
    - Read the full text of a PAD, ISR, Aide Mémoire, or other WB document
    - Extract specific sections for analysis (e.g. FCV risk sections in a PAD)
    - Feed document content into further analysis (e.g. FCV screening workflow)

    Requires a valid PDF URL obtained from wb_search_documents or wb_get_project_documents.
    Note: Some documents are scanned images — these will return minimal extractable text.

    Args:
        params (FetchDocumentInput): pdf_url (required, from search results) and
            max_chars (optional, default 50000 — reduce for faster responses).

    Returns:
        str: Extracted text content from the PDF, truncated to max_chars if needed.
             Includes page count and a warning if the document appears to be image-only.
    """
    try:
        async with _make_client() as client:
            resp = await client.get(params.pdf_url)
            resp.raise_for_status()
            pdf_bytes = resp.content
    except Exception as e:
        return _handle_api_error(e)

    # Extract text using pypdf if available, else fall back to raw byte inspection
    try:
        import io
        import pypdf

        reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
        page_count = len(reader.pages)
        pages_text = []

        for page in reader.pages:
            try:
                pages_text.append(page.extract_text() or "")
            except Exception:
                pages_text.append("")

        full_text = "\n\n".join(pages_text).strip()

        # Detect image-only PDFs
        is_image_only = len(full_text) < 200 and page_count > 1

        header = f"**Document extracted — {page_count} pages**\n\n"
        if is_image_only:
            header += (
                "⚠️ *This document appears to be a scanned image PDF. "
                "Very little text could be extracted. "
                "You may need to use OCR software for full content.*\n\n"
            )

        if len(full_text) > params.max_chars:
            full_text = full_text[:params.max_chars]
            header += f"*Text truncated to {params.max_chars:,} characters (document has more content).*\n\n"

        return header + (full_text or "[No extractable text found in this document.]")

    except ImportError:
        # pypdf not installed — return base64 hint
        b64 = base64.b64encode(pdf_bytes).decode("utf-8")
        return (
            f"PDF downloaded ({len(pdf_bytes):,} bytes) but pypdf is not installed for text extraction.\n"
            f"Install pypdf: `pip install pypdf`\n\n"
            f"Base64 content (first 500 chars): {b64[:500]}..."
        )
    except Exception as e:
        return f"Error extracting text from PDF: {e}. The document may be password-protected or corrupted."


@mcp.tool(
    name="wb_list_document_types",
    annotations={
        "title": "List Valid World Bank Document Types",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def wb_list_document_types(params: ListDocumentTypesInput) -> str:
    """Return the list of valid document type strings for use in search filters.

    Use this tool before calling wb_search_documents or wb_get_project_documents
    if you're unsure what document type strings to use. The strings must match
    exactly when passed as doc_types filters.

    Args:
        params (ListDocumentTypesInput): response_format ('markdown' or 'json').

    Returns:
        str: List of valid document type strings accepted by the World Bank API.
    """
    if params.response_format == "json":
        return json.dumps({"document_types": DOCUMENT_TYPES}, indent=2)

    lines = ["## Valid World Bank Document Types\n"]
    lines += [f"- `{dt}`" for dt in DOCUMENT_TYPES]
    lines += [
        "\n**Usage tip:** Pass these exact strings in the `doc_types` parameter of",
        "`wb_search_documents` or `wb_get_project_documents`.",
        "\n**Most useful for FCV screening:**",
        "- `Project Appraisal Document` — main project design document",
        "- `Implementation Status and Results Report` — progress updates during implementation",
        "- `Aide Memoire` — mission aide mémoires (often not publicly disclosed)",
        "- `Project Concept Note` / `Project Information Document` — early pipeline documents",
        "- `Restructuring Paper` — when project design changes mid-implementation",
    ]
    return "\n".join(lines)


# ─── Entry point ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    port = int(os.environ.get("PORT", 8000))
    mcp.run(transport="streamable-http", host="0.0.0.0", port=port)
