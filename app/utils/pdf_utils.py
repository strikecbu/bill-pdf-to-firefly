import pdfplumber
import structlog

logger = structlog.get_logger()


def extract_all_tables(pdf_path: str) -> list:
    """Extract all tables from a PDF file."""
    tables = []
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                page_tables = page.extract_tables()
                for table in page_tables:
                    tables.append({"page": page_num + 1, "data": table})
    except Exception as e:
        logger.error("pdf_table_extraction_error", error=str(e))
    return tables


def extract_text(pdf_path: str) -> str:
    """Extract all text from a PDF file."""
    text = ""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text()
                if page_text:
                    text += page_text + "\n"
    except Exception as e:
        logger.error("pdf_text_extraction_error", error=str(e))
    return text
