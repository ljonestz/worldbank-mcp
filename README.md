# worldbank-mcp

An MCP (Model Context Protocol) server that provides tools to search and retrieve World Bank project documents and project data from the World Bank's public Open Data APIs. No authentication required.

## Tools

| Tool | Description |
|------|-------------|
| `wb_search_documents` | Search the World Bank Documents & Reports API by keyword, country, project ID, document type, and date range |
| `wb_search_projects` | Search the World Bank Projects API by country, sector, status, or keyword |
| `wb_get_project_documents` | Retrieve all documents for a specific project ID (e.g. P123456) |
| `wb_fetch_document_text` | Download a World Bank PDF and extract its text content |
| `wb_list_document_types` | List valid document type strings for use as search filters |

## Setup

```bash
# Install dependencies
pip install -r requirements.txt

# Run the server locally
python server.py
```

The server starts on port 8000 by default. Set `PORT` environment variable to override.

## Deployment

Deploy on [Render.com](https://render.com) or any platform that supports Python. The `Procfile` and `runtime.txt` are included for Render deployment.

## Usage example

Once connected to an MCP-enabled client (e.g. Claude Code), you can:

- Find PADs for a country: search documents with `country_code="SO"` and `doc_types=["Project Appraisal Document"]`
- Get all documents for a project: call `wb_get_project_documents` with `project_id="P123456"`
- Extract text from a PAD PDF: call `wb_fetch_document_text` with the `pdf_url` from search results

## Notes

- All World Bank documents accessed through this server are publicly available — no authentication needed
- PDF text extraction uses `pypdf`; scanned image PDFs will return minimal text
- Results support both `markdown` (human-readable) and `json` (machine-readable) response formats
