
import fitz
from pathlib import Path


def main():
    # Enter the PDF path when prompted
    pdf_path = Path(
        input("Enter the full PDF file path: ").strip().strip('"')
    )

    if not pdf_path.is_file():
        print(f"Error: File not found: {pdf_path}")
        return

    if pdf_path.suffix.lower() != ".pdf":
        print("Error: Please provide a PDF file.")
        return

    # Save output on your Windows Desktop
    desktop = Path.home() / "Desktop"
    output_dir = desktop / f"{pdf_path.stem}_split_pages"
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        with fitz.open(pdf_path) as pdf:
            total_pages = len(pdf)

            for i in range(total_pages):
                output_path = output_dir / f"page_{i + 1:04d}.pdf"

                with fitz.open() as output_pdf:
                    output_pdf.insert_pdf(
                        pdf,
                        from_page=i,
                        to_page=i
                    )
                    output_pdf.save(output_path)

                print(f"Saved page {i + 1}/{total_pages}")

        print(f"\nSuccessfully split {total_pages} pages.")
        print(f"Output folder: {output_dir}")

    except Exception as e:
        print(f"Error processing PDF: {e}")


if __name__ == "__main__":
    main()
