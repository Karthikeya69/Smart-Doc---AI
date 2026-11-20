# Automated RAG evaluation report

- Run: 2026-11-20 11:22:27
- Target: http://smartdoc-eduleem.duckdns.org
- Result: **10/10 passed**
- AI model answered: 6, labelled extractive fallback answered: 2

| ID | Test | Result | Pages cited | Answered by |
|---|---|---|---|---|
| T1 | Annual leave entitlement | PASS | 1, 2 | fallback |
| T2 | Sick leave eligibility | PASS | 1, 2 | LLM |
| T3 | Maternity leave policy | PASS | 1, 2 | fallback |
| T4 | Notice period | PASS | 1, 2, 3 | LLM |
| T5 | IT security guidelines | PASS | 1, 3 | LLM |
| T6 | Multi-section question | PASS | 1, 2 | LLM |
| T7 | Not covered in document | PASS | - | n/a |
| T8 | Private chat is isolated | PASS | - | n/a |
| T9 | Document summary | PASS | 1, 2, 3 | LLM |
| T10 | Typo tolerance | PASS | 1, 2 | LLM |

## Answers

### T1 Annual leave entitlement - PASS
Question: What is the annual leave entitlement?

> Note: the AI model is unavailable, so these are the closest passages.
> 
> - Annual Leave Every confirmed employee is entitled to 24 days of paid annual leave per calendar year. (Sample_HR_Policy.pdf, Page 1)
> 
> - Annual leave accrues at the rate of 2 days for every completed month of service. (Sample_HR_Policy.pdf, Page 1)
> 
> - Annual leave accrues at the rate of 2 days for every completed month of service. (Sample_HR_Policy.pdf, Page 1)

### T2 Sick leave eligibility - PASS
Question: How many sick leaves are employees eligible for?

> Employees are eligible for 12 days of paid sick leave per calendar year. 
> 
> Any unused sick leave does not carry forward to the following year. If an employee takes more than two consecutive days of sick leave, a medical certificate from a registered doctor is required, and the reporting manager must be notified before 10:00 AM on the first day of absence (Sample_HR_Policy.pdf, Page 2).

### T3 Maternity leave policy - PASS
Question: What is the maternity leave policy?

> Note: the AI model is unavailable, so these are the closest passages.
> 
> - Maternity Leave Female employees are entitled to 26 weeks of paid maternity leave for the first two children and 12 weeks for any subsequent child. (Sample_HR_Policy.pdf, Page 2)
> 
> - Maternity leave can begin up to 8 weeks before the expected delivery date. (Sample_HR_Policy.pdf, Page 2)
> 
> - ild under the age of 3 months is entitled to 12 weeks of paid adoption leave. (Sample_HR_Policy.pdf, Page 2)

### T4 Notice period - PASS
Question: What is the employee notice period?

> The notice period is 60 days for confirmed employees and 30 days for those currently serving their six-month probation period. Either party may waive this notice period, but only with written approval from the HR director (Sample_HR_Policy.pdf, Page 3).

### T5 IT security guidelines - PASS
Question: What are the company's IT security guidelines?

> The company requires employees to adhere to several strict protocols regarding account security, data handling, and incident reporting. 
> 
> Under the IT security guidelines, employees must follow these key rules:
> * Maintain passwords of at least 12 characters and never share them with anyone, including colleagues or IT staff.
> * Enable multi-factor authentication on all company accounts.
> * Store company data solely on approved cloud storage, avoiding personal USB drives and personal email for company files.
> * Lock computer screens whenever stepping away from the desk.
> * Report any suspected security incidents, such as phishing attempts or lost laptops, to the IT security team within 24 hours (Sample_HR_Policy.pdf, Page 3).

### T6 Multi-section question - PASS
Question: Compare annual leave, sick leave and casual leave

> Employees are entitled to 24 days of annual leave, 12 days of sick leave, and 6 days of casual leave per calendar year, each serving different purposes and having distinct carry-forward and notification rules. Looking at the numbers, annual leave offers the largest entitlement at 24 days (accruing at 2 days per month), which is double the 12-day sick leave allocation and four times the 6-day casual leave allowance. 
> 
> The primary distinctions between the three leave types are:
> 
> * Annual leave is meant for planned time off, permitting up to 10 unused days to carry forward to the next year (any excess lapses without encashment), and requires at least 5 working days' advance notice for absences longer than 3 consecutive days (Sample_HR_Policy.pdf, Page 1).
> * Sick leave covers health issues for 12 days, cannot be carried forward at all, requires you to inform your manager before 10:00 AM on the first day, and necessitates a doctor's certificate if taken for more than 2 consecutive days (Sample_HR_Policy.pdf, Page 2).
> * Casual leave provides 6 days for personal matters and cannot be combined with annual leave for more than 5 consecutive days (Sample_HR_Policy.pdf, Page 2).

### T7 Not covered in document - PASS
Question: What is the capital of France?

> Information not available in the uploaded documents.

### T8 Private chat is isolated - PASS
Question: What is the annual leave entitlement?  (scope: isolated)

> Information not available in the uploaded documents.

### T9 Document summary - PASS
Question: Give me a summary of Sample_HR_Policy.pdf

> This document is a sample employee HR policy handbook for Northwind Technologies Pvt Ltd, designed to explain the working hours, leave policies, notice periods, IT guidelines, and conduct rules applicable to all full-time employees.
> 
> * Standard working hours are Monday to Friday from 9:30 AM to 6:30 PM with a one-hour lunch break, requiring daily attendance logging (Sample_HR_Policy.pdf, Page 1).
> * Employees receive 24 days of paid annual leave per year (accruing at 2 days per month

### T10 Typo tolerance - PASS
Question: What is the anual entitlment?

> Every confirmed employee is entitled to 24 days of paid annual leave per calendar year. 
> 
> This accrues at a rate of 2 days for each completed month of service, which totals 24 days across the 12-month period. You can carry forward up to 10 unused days into the next calendar year, but any balance exceeding 10 days lapses on December 31 and cannot be encashed (Sample_HR_Policy.pdf, Page 1).
