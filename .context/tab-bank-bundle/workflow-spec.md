# Tab Bank Bundle — Original Workflow Spec

> Source: workflow authored by Jaehyeon Park (converted from `PDF Merge Workflow for Tab Bank.txt`, verbatim wording preserved).
> Context: daily routine that builds invoice + POD packets for upload to Tab Bank (invoice factoring).

## 1. Initialization Phase

### 1.1 Folder Setup
Create folder:
Desktop\today invoices
Get current system date
Convert to format: yyMMdd → FormattedDateTime
Create subfolder:
Desktop\today invoices\{FormattedDateTime}

### 1.2 Browser Initialization
Launch Chrome (Session 1) → QBO (QuickBooks)
Launch Chrome (Session 2) → TMS
Focus QBO window
Press Enter (to bypass login prompt if needed)
Wait for login to complete

### 1.3 Excel Initialization
Open:
NGL INVOICES.xlsx
Select sheet:
NGL INVOICE
Set column R header = PROGRESS
Read entire table into memory

## 2. Main Loop (Row-by-Row Processing)

Loop starts from row 2:

### 2.1 Exit Condition
If INV # is empty:
Close QBO and TMS
Exit loop

### 2.2 Skip Logic
If PROGRESS = Processed:
Skip row
Move to next row

### 2.3 Variable Initialization

Extract from row:

CurrentINV
CurrentName
CurrentDate
CurrentWO
QB ID

Derived:

FormattedDate = yyMMdd(CurrentDate)
CurrentINVFirstChar
WOSecondChar
FileFound = False
SourceSystem = ""
TargetSuffix = ""

## 3. QBO Processing (Primary Source)

### 3.1 Navigate to Invoice
Build URL:
https://qbo.intuit.com/app/invoice?txnId={QB ID}
Navigate (retry if failure)
Wait for page load

### 3.2 Extract Attachment List
Collect all attachment names (PDF only)
Convert to lowercase string

### 3.3 Determine TargetSuffix (QBO Rules)

Priority logic:

If contains "pod" → pod
Else if "ite" → ite
Else if "it" → it
Else if company = HEALTHY GREEN LIFE CORP.
"pol" → pol
"bl" → bl
Else if invoice starts with S
"pol" → pol
Else → x

### 3.4 QBO Success Condition
If TargetSuffix != x:
FileFound = True
SourceSystem = QB

## 4. TMS Fallback (Only if QBO failed)

### 4.1 Determine TMS URL
If WOSecondChar = M:
Import page
If WOSecondChar = X:
Export page

### 4.2 Navigate to TMS Page
Load WO document page
Wait for elements

### 4.3 Identify Available Documents
Scan visible buttons:
DO, POD, POL, BL, IT, ITE
Ignore disabled buttons
Ignore DO

### 4.4 Determine TargetSuffix (TMS Rules)

Priority:

POD
IT
ITE
If company = HGL:
POL, then BL
If INV starts with S:
POL
Else → x

### 4.5 TMS Validation Rules

Set FileFound = True only if:

pod, it, ite → always valid
pol:
only if HGL OR INV starts with S
bl:
only if HGL

Else:

FileFound = False

## 5. Download Phase

### 5.1 If FileFound = TRUE

#### 5.1.1 Download QBO Main Invoice
Click: Print or download
Click: Download
Save as:
Invoice {CurrentINV}.pdf

#### 5.1.2 Download Attachment
Case A: Source = QBO
Find best matching attachment:
Exact match preferred
Avoid it matching ite
Download using fetch()
Save as:
{CurrentINV}_{TargetSuffix}.pdf
Case B: Source = TMS
Click corresponding document button
Download file:
{CurrentWO}_{SUFFIX}.pdf
Rename to:
{CurrentINV}_{TargetSuffix}.pdf

### 5.2 Merge PDFs
Files to merge:
Attachment PDF
Invoice PDF
Output:
Desktop\today invoices\{FormattedDate}\{CurrentINV}.pdf

### 5.3 Cleanup
Delete:
Invoice PDF (Downloads)
Attachment PDF (Downloads)

## 6. Failure Handling

### 6.1 If FileFound = FALSE
Highlight Excel row (A–Q)
Color: Pink

## 7. Finalization Per Row
Write:
PROGRESS = Processed
Save Excel
Move to next row
