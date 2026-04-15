---
name: XLSX
description: "Use this skill when creating, editing, or analyzing Excel spreadsheets (.xlsx files). Triggers: Excel, spreadsheet, workbook, xlsx, financial model, data analysis, pivot table, formula. Covers: openpyxl for formulas/formatting, pandas for data analysis, financial modeling standards, formula verification."
---

# XLSX creation, editing, and analysis

## Overview

A user may ask you to create, edit, or analyze the contents of an .xlsx file.

## Dependencies

**Required (always available):**
- `pip install openpyxl` - Excel creation/editing with formulas and formatting
- `pip install pandas` - Data analysis and bulk operations

**Optional (for formula recalculation):**
- LibreOffice — auto-configured via `scripts/office/soffice.py`
- Used by `scripts/recalc.py` to recalculate formulas after file creation

> **Note:** Core create/edit/analyze functionality works without LibreOffice. Only formula recalculation requires it.

---

## Important Requirements

**LibreOffice Required for Formula Recalculation**: The `scripts/recalc.py` script automatically configures LibreOffice on first run, including in sandboxed environments.

## Reading and analyzing data

### Data analysis with pandas

```python
import pandas as pd

# Read Excel
df = pd.read_excel('file.xlsx')  # Default: first sheet
all_sheets = pd.read_excel('file.xlsx', sheet_name=None)  # All sheets as dict

# Analyze
df.head()      # Preview data
df.info()      # Column info
df.describe()  # Statistics

# Write Excel
df.to_excel('output.xlsx', index=False)
```

---

## CRITICAL: Use Formulas, Not Hardcoded Values

**Always use Excel formulas instead of calculating values in Python and hardcoding them.** This ensures the spreadsheet remains dynamic and updateable.

### ✅ CORRECT - Using Excel Formulas

```python
# Good: Let Excel calculate the sum
sheet['B10'] = '=SUM(B2:B9)'

# Good: Growth rate as Excel formula
sheet['C5'] = '=(C4-C2)/C2'

# Good: Average using Excel function
sheet['D20'] = '=AVERAGE(D2:D19)'
```

### ❌ WRONG - Hardcoding Calculated Values

```python
# Bad: Calculating in Python and hardcoding result
total = df['Sales'].sum()
sheet['B10'] = total  # Hardcodes 5000
```

---

## Common Workflow

1. **Choose tool**: pandas for data, openpyxl for formulas/formatting
2. **Create/Load**: Create new workbook or load existing file
3. **Modify**: Add/edit data, formulas, and formatting
4. **Save**: Write to file
5. **Recalculate formulas (MANDATORY IF USING FORMULAS)**:
   ```bash
   python scripts/recalc.py output.xlsx
   ```
6. **Verify and fix any errors**

### Creating new Excel files

```python
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment

wb = Workbook()
sheet = wb.active

# Add data
sheet['A1'] = 'Hello'
sheet['B1'] = 'World'
sheet.append(['Row', 'of', 'data'])

# Add formula
sheet['B2'] = '=SUM(A1:A10)'

# Formatting
sheet['A1'].font = Font(bold=True, color='FF0000')
sheet['A1'].fill = PatternFill('solid', start_color='FFFF00')
sheet['A1'].alignment = Alignment(horizontal='center')

# Column width
sheet.column_dimensions['A'].width = 20

wb.save('output.xlsx')
```

### Editing existing Excel files

```python
from openpyxl import load_workbook

wb = load_workbook('existing.xlsx')
sheet = wb.active

# Modify cells
sheet['A1'] = 'New Value'

# Add new sheet
new_sheet = wb.create_sheet('NewSheet')
new_sheet['A1'] = 'Data'

wb.save('modified.xlsx')
```

---

## Financial Model Standards

### Color Coding

Unless otherwise stated by the user or existing template:

- **Blue text (RGB: 0,0,255)**: Hardcoded inputs, and numbers users will change for scenarios
- **Black text (RGB: 0,0,0)**: ALL formulas and calculations
- **Green text (RGB: 0,128,0)**: Links pulling from other worksheets within same workbook
- **Red text (RGB: 255,0,0)**: External links to other files
- **Yellow background (RGB: 255,255,0)**: Key assumptions needing attention

### Number Formatting Standards

- **Years**: Format as text strings (e.g., "2024" not "2,024")
- **Currency**: Use $#,##0 format; ALWAYS specify units in headers ("Revenue ($mm)")
- **Zeros**: Use number formatting to make all zeros "-", including percentages
- **Percentages**: Default to 0.0% format (one decimal)
- **Multiples**: Format as 0.0x for valuation multiples (EV/EBITDA, P/E)
- **Negative numbers**: Use parentheses (123) not minus -123

### Formula Construction Rules

- Place ALL assumptions in separate cells
- Use cell references instead of hardcoded values in formulas
- Example: Use =B5*(1+$B$6) instead of =B5*1.05
- Document hardcoded values with source citations

---

## Recalculating formulas

```bash
python scripts/recalc.py output.xlsx 30
```

The script returns JSON with error details:
```json
{
  "status": "success",           // or "errors_found"
  "total_errors": 0,
  "total_formulas": 42,
  "error_summary": {}             // Only present if errors found
}
```

## Formula Verification Checklist

- **Test 2-3 sample references**: Verify they pull correct values
- **Column mapping**: Confirm Excel columns match (column 64 = BL, not BK)
- **Row offset**: Excel rows are 1-indexed (DataFrame row 5 = Excel row 6)
- **Division by zero**: Check denominators before using `/`
- **Cross-sheet references**: Use correct format (Sheet1!A1)

## Best Practices

### Library Selection
- **pandas**: Best for data analysis, bulk operations, and simple data export
- **openpyxl**: Best for complex formatting, formulas, and Excel-specific features

### Working with openpyxl
- Cell indices are 1-based (row=1, column=1 refers to cell A1)
- Use `data_only=True` to read calculated values
- **Warning**: If opened with `data_only=True` and saved, formulas are replaced with values permanently
- For large files: Use `read_only=True` for reading or `write_only=True` for writing
- Formulas are preserved but not evaluated - use scripts/recalc.py to update values

## Professional Font

Use a consistent, professional font (e.g., Arial, Times New Roman) for all deliverables unless otherwise instructed.

## Zero Formula Errors

Every Excel model MUST be delivered with ZERO formula errors (#REF!, #DIV/0!, #VALUE!, #N/A, #NAME?).
