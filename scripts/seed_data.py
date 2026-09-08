#!/usr/bin/env python3
"""Seed deterministic synthetic Bluey data into DynamoDB.

This script only writes the deterministic records declared below. It never scans
or deletes a table, so rerunning it updates the demo keys without removing other
data in the account. Use --dry-run to inspect the records before writing them.
"""
# Seed records are kept compact; long literal rows are intentional.
# ruff: noqa: E501

import argparse
import sys
from decimal import Decimal

import boto3

TABLE_NAMES = {
    "customers": "bluey-customers",
    "accounts": "bluey-accounts",
    "transactions": "bluey-transactions",
    "sessions": "bluey-sessions",
    "bankers": "bluey-bankers",
    "credit": "bluey-credit",
    "applications": "bluey-applications",
}


def customer(customer_id, name, email, phone, id_number, banker, **features):
    first_name, *last_name = name.split()
    return {
        "customerId": customer_id,
        "fullName": name,
        "first_name": first_name,
        "last_name": " ".join(last_name),
        "email": email,
        "phone": phone,
        "idNumber": id_number,
        "isDemo": True,
        "personalBanker": banker,
        "creditScore": features["credit_score"],
        **features,
    }


BANKERS = [
    {
        "bankerId": "banker-001",
        "name": "Lindiwe Dube",
        "email": "lindiwe.dube@standardbank.co.za",
        "tier": "general",
    },
    {
        "bankerId": "banker-002",
        "name": "Sipho Mthembu",
        "email": "sipho.mthembu@standardbank.co.za",
        "tier": "general",
    },
    {
        "bankerId": "banker-003",
        "name": "Themba Ndlovu",
        "email": "themba.ndlovu@standardbank.co.za",
        "tier": "premium",
    },
]


CUSTOMERS = [
    customer(
        "cust-001",
        "Thabo Nkosi",
        "thabo.nkosi@example.com",
        "0820001001",
        "9001011234089",
        BANKERS[0],
        age=33, gender="Male", marital_status="Married", number_of_dependents=2,
        education_level="Diploma", province="Gauteng", employment_status="Employed",
        monthly_income=18500, income_band="LSM 5-6", monthly_expenses=12100,
        account_tenure_years=7, monthly_transactions=22, avg_transaction_value=550,
        digital_login_frequency=14, branch_visit_count=3, late_payment_count=2,
        preferred_channel="Digital-Preferred", has_savings_account=1, has_credit_card=1,
        has_home_loan=0, has_personal_loan=1, has_vehicle_finance=1,
        has_investment_account=0, active_products_count=4, has_mymo_account=0,
        has_achieva_account=1, has_prestige_banking=0, has_private_banking=0,
        has_signature_banking=0, has_blue_credit_card=0, has_gold_credit_card=1,
        has_titanium_credit_card=0, has_platinum_credit_card=0,
        has_world_citizen_credit_card=0, has_diners_club_platinum_credit_card=0,
        has_term_loan=1, has_revolving_loan=0, has_overdraft=0,
        has_investment_backed_lending=0, has_student_loan=0, has_fixed_deposit=0,
        has_tax_free_savings=0, credit_score=612, loan_utilisation_rate=0.42,
        days_since_last_default=180, has_ever_defaulted=0,
        savings_rate=round((18500 - 12100) / 18500, 4),
    ),
    customer(
        "cust-002",
        "Priya Singh",
        "priya.singh@example.com",
        "0820001002",
        "8502022345090",
        BANKERS[2],
        age=41, gender="Female", marital_status="Married", number_of_dependents=2,
        education_level="Postgraduate", province="Gauteng", employment_status="Employed",
        monthly_income=68000, income_band="LSM 9-10", monthly_expenses=38000,
        account_tenure_years=12, monthly_transactions=30, avg_transaction_value=1800,
        digital_login_frequency=25, branch_visit_count=0, late_payment_count=0,
        preferred_channel="Digital-Preferred", has_savings_account=1, has_credit_card=1,
        has_home_loan=1, has_personal_loan=0, has_vehicle_finance=0,
        has_investment_account=1, active_products_count=6, has_mymo_account=0,
        has_achieva_account=0, has_prestige_banking=1, has_private_banking=0,
        has_signature_banking=0, has_blue_credit_card=0, has_gold_credit_card=0,
        has_titanium_credit_card=0, has_platinum_credit_card=1,
        has_world_citizen_credit_card=0, has_diners_club_platinum_credit_card=0,
        has_term_loan=0, has_revolving_loan=0, has_overdraft=0,
        has_investment_backed_lending=0, has_student_loan=0, has_fixed_deposit=1,
        has_tax_free_savings=1, credit_score=705, loan_utilisation_rate=0.15,
        days_since_last_default=9999, has_ever_defaulted=0,
        savings_rate=round((68000 - 38000) / 68000, 4),
    ),
    customer(
        "cust-003",
        "Johan van der Merwe",
        "johan.vdm@example.com",
        "0820001003",
        "9103033456091",
        BANKERS[0],
        age=35, gender="Male", marital_status="Married", number_of_dependents=1,
        education_level="Matric", province="Western Cape", employment_status="Employed",
        monthly_income=32000, income_band="LSM 7-8", monthly_expenses=24000,
        account_tenure_years=6, monthly_transactions=24, avg_transaction_value=900,
        digital_login_frequency=15, branch_visit_count=2, late_payment_count=1,
        preferred_channel="Digital-Preferred", has_savings_account=1, has_credit_card=0,
        has_home_loan=0, has_personal_loan=0, has_vehicle_finance=1,
        has_investment_account=0, active_products_count=3, has_mymo_account=0,
        has_achieva_account=1, has_prestige_banking=0, has_private_banking=0,
        has_signature_banking=0, has_blue_credit_card=0, has_gold_credit_card=0,
        has_titanium_credit_card=0, has_platinum_credit_card=0,
        has_world_citizen_credit_card=0, has_diners_club_platinum_credit_card=0,
        has_term_loan=0, has_revolving_loan=0, has_overdraft=0,
        has_investment_backed_lending=0, has_student_loan=0, has_fixed_deposit=0,
        has_tax_free_savings=0, credit_score=640, loan_utilisation_rate=0.30,
        days_since_last_default=9999, has_ever_defaulted=0,
        savings_rate=round((32000 - 24000) / 32000, 4),
    ),
    customer(
        "cust-004",
        "Zara Abrahams",
        "zara.abrahams@example.com",
        "0820001004",
        "0204044567092",
        BANKERS[1],
        age=24, gender="Female", marital_status="Single", number_of_dependents=1,
        education_level="No Matric", province="KwaZulu-Natal", employment_status="Unemployed",
        monthly_income=4200, income_band="LSM 1-4", monthly_expenses=4000,
        account_tenure_years=1, monthly_transactions=10, avg_transaction_value=150,
        digital_login_frequency=8, branch_visit_count=3, late_payment_count=3,
        preferred_channel="Branch", has_savings_account=0, has_credit_card=0,
        has_home_loan=0, has_personal_loan=1, has_vehicle_finance=0,
        has_investment_account=0, active_products_count=2, has_mymo_account=1,
        has_achieva_account=0, has_prestige_banking=0, has_private_banking=0,
        has_signature_banking=0, has_blue_credit_card=0, has_gold_credit_card=0,
        has_titanium_credit_card=0, has_platinum_credit_card=0,
        has_world_citizen_credit_card=0, has_diners_club_platinum_credit_card=0,
        has_term_loan=1, has_revolving_loan=0, has_overdraft=0,
        has_investment_backed_lending=0, has_student_loan=0, has_fixed_deposit=0,
        has_tax_free_savings=0, credit_score=480, loan_utilisation_rate=0.65,
        days_since_last_default=45, has_ever_defaulted=1,
        savings_rate=round((4200 - 4000) / 4200, 4),
    ),
    customer(
        "cust-005",
        "Thabo Molefe",
        "thabo.molefe@example.com",
        "0820001005",
        "9805055678093",
        BANKERS[1],
        age=27, gender="Male", marital_status="Married", number_of_dependents=5,
        education_level="Diploma", province="Free State", employment_status="Employed",
        monthly_income=12000, income_band="LSM 5-6", monthly_expenses=8500,
        account_tenure_years=5, monthly_transactions=22, avg_transaction_value=80,
        digital_login_frequency=8, branch_visit_count=10, late_payment_count=4,
        preferred_channel="Branch", has_savings_account=1, has_credit_card=0,
        has_home_loan=0, has_personal_loan=0, has_vehicle_finance=0,
        has_investment_account=0, active_products_count=2, has_mymo_account=1,
        has_achieva_account=0, has_prestige_banking=0, has_private_banking=0,
        has_signature_banking=0, has_blue_credit_card=0, has_gold_credit_card=0,
        has_titanium_credit_card=0, has_platinum_credit_card=0,
        has_world_citizen_credit_card=0, has_diners_club_platinum_credit_card=0,
        has_term_loan=1, has_revolving_loan=0, has_overdraft=0,
        has_investment_backed_lending=0, has_student_loan=0, has_fixed_deposit=0,
        has_tax_free_savings=0, credit_score=520, loan_utilisation_rate=0.50,
        days_since_last_default=999, has_ever_defaulted=1,
        savings_rate=round((12000 - 8500) / 12000, 4),
    ),
    customer(
        "cust-006",
        "Lerato Mokoena",
        "lerato.mokoena@example.com",
        "0820001006",
        "9506066789094",
        BANKERS[0],
        age=31, gender="Female", marital_status="Single", number_of_dependents=0,
        education_level="Degree", province="Gauteng", employment_status="Employed",
        monthly_income=24500, income_band="LSM 7-8", monthly_expenses=14200,
        account_tenure_years=4, monthly_transactions=28, avg_transaction_value=620,
        digital_login_frequency=21, branch_visit_count=1, late_payment_count=0,
        preferred_channel="Digital-Preferred", has_savings_account=1, has_credit_card=1,
        has_home_loan=0, has_personal_loan=1, has_vehicle_finance=0,
        has_investment_account=0, active_products_count=4, has_mymo_account=0,
        has_achieva_account=1, has_prestige_banking=0, has_private_banking=0,
        has_signature_banking=0, has_blue_credit_card=0, has_gold_credit_card=0,
        has_titanium_credit_card=1, has_platinum_credit_card=0,
        has_world_citizen_credit_card=0, has_diners_club_platinum_credit_card=0,
        has_term_loan=1, has_revolving_loan=0, has_overdraft=0,
        has_investment_backed_lending=0, has_student_loan=0, has_fixed_deposit=0,
        has_tax_free_savings=1, credit_score=674, loan_utilisation_rate=0.28,
        days_since_last_default=9999, has_ever_defaulted=0,
        savings_rate=round((24500 - 14200) / 24500, 4),
    ),
]


ACCOUNTS = [
    {
        "customerId": "cust-001",
        "accountId": "acc-001",
        "accountType": "Savings",
        "accountNumber": "1234567890",
        "balance": Decimal("15400.50"),
        "currency": "ZAR",
    },
    {
        "customerId": "cust-001",
        "accountId": "acc-002",
        "accountType": "Cheque",
        "accountNumber": "1234567891",
        "balance": Decimal("3200.00"),
        "currency": "ZAR",
    },
    {
        "customerId": "cust-002",
        "accountId": "acc-003",
        "accountType": "Fixed Deposit",
        "accountNumber": "1234567892",
        "balance": Decimal("75000.00"),
        "currency": "ZAR",
    },
    {
        "customerId": "cust-003",
        "accountId": "acc-004",
        "accountType": "Savings",
        "accountNumber": "1234567893",
        "balance": Decimal("8200.75"),
        "currency": "ZAR",
    },
    {
        "customerId": "cust-004",
        "accountId": "acc-005",
        "accountType": "Cheque",
        "accountNumber": "1234567894",
        "balance": Decimal("1250.00"),
        "currency": "ZAR",
    },
    {
        "customerId": "cust-005",
        "accountId": "acc-006",
        "accountType": "Savings",
        "accountNumber": "1234567895",
        "balance": Decimal("4680.20"),
        "currency": "ZAR",
    },
    {
        "customerId": "cust-006",
        "accountId": "acc-007",
        "accountType": "Cheque",
        "accountNumber": "1234567896",
        "balance": Decimal("18950.40"),
        "currency": "ZAR",
    },
]


TRANSACTIONS = [
    {
        "accountId": "acc-001",
        "date#transactionId": "2026-08-25#txn-001",
        "description": "Salary deposit",
        "amount": Decimal("18500.00"),
        "category": "Income",
    },
    {
        "accountId": "acc-001",
        "date#transactionId": "2026-08-27#txn-002",
        "description": "Groceries - Checkers",
        "amount": Decimal("-850.50"),
        "category": "Shopping",
    },
    {
        "accountId": "acc-001",
        "date#transactionId": "2026-08-29#txn-003",
        "description": "Electricity bill",
        "amount": Decimal("-1200.00"),
        "category": "Utilities",
    },
    {
        "accountId": "acc-002",
        "date#transactionId": "2026-08-26#txn-004",
        "description": "Debit order - fibre",
        "amount": Decimal("-799.00"),
        "category": "Utilities",
    },
    {
        "accountId": "acc-002",
        "date#transactionId": "2026-08-28#txn-005",
        "description": "Fuel - Engen",
        "amount": Decimal("-650.00"),
        "category": "Transport",
    },
    {
        "accountId": "acc-002",
        "date#transactionId": "2026-08-30#txn-006",
        "description": "Payment received",
        "amount": Decimal("1200.00"),
        "category": "Income",
    },
    {
        "accountId": "acc-003",
        "date#transactionId": "2026-08-15#txn-007",
        "description": "Interest payment",
        "amount": Decimal("450.00"),
        "category": "Income",
    },
    {
        "accountId": "acc-003",
        "date#transactionId": "2026-08-20#txn-008",
        "description": "Fixed deposit renewal",
        "amount": Decimal("-2500.00"),
        "category": "Savings",
    },
    {
        "accountId": "acc-003",
        "date#transactionId": "2026-08-31#txn-009",
        "description": "Interest payment",
        "amount": Decimal("462.50"),
        "category": "Income",
    },
    {
        "accountId": "acc-004",
        "date#transactionId": "2026-08-22#txn-010",
        "description": "Salary deposit",
        "amount": Decimal("32000.00"),
        "category": "Income",
    },
    {
        "accountId": "acc-004",
        "date#transactionId": "2026-08-24#txn-011",
        "description": "Online purchase - Takealot",
        "amount": Decimal("-650.00"),
        "category": "Shopping",
    },
    {
        "accountId": "acc-004",
        "date#transactionId": "2026-08-30#txn-012",
        "description": "Restaurant - Cape Town",
        "amount": Decimal("-480.00"),
        "category": "Food",
    },
    {
        "accountId": "acc-005",
        "date#transactionId": "2026-08-21#txn-013",
        "description": "Social grant deposit",
        "amount": Decimal("4200.00"),
        "category": "Income",
    },
    {
        "accountId": "acc-005",
        "date#transactionId": "2026-08-23#txn-014",
        "description": "Taxi fare",
        "amount": Decimal("-180.00"),
        "category": "Transport",
    },
    {
        "accountId": "acc-005",
        "date#transactionId": "2026-08-29#txn-015",
        "description": "Mobile data",
        "amount": Decimal("-299.00"),
        "category": "Utilities",
    },
    {
        "accountId": "acc-006",
        "date#transactionId": "2026-08-20#txn-016",
        "description": "Salary deposit",
        "amount": Decimal("12000.00"),
        "category": "Income",
    },
    {
        "accountId": "acc-006",
        "date#transactionId": "2026-08-25#txn-017",
        "description": "Rent payment",
        "amount": Decimal("-6200.00"),
        "category": "Housing",
    },
    {
        "accountId": "acc-006",
        "date#transactionId": "2026-08-28#txn-018",
        "description": "Groceries - Pick n Pay",
        "amount": Decimal("-740.00"),
        "category": "Shopping",
    },
    {
        "accountId": "acc-007",
        "date#transactionId": "2026-08-19#txn-019",
        "description": "Salary deposit",
        "amount": Decimal("24500.00"),
        "category": "Income",
    },
    {
        "accountId": "acc-007",
        "date#transactionId": "2026-08-23#txn-020",
        "description": "Home loan instalment",
        "amount": Decimal("-6800.00"),
        "category": "Housing",
    },
    {
        "accountId": "acc-007",
        "date#transactionId": "2026-08-29#txn-021",
        "description": "Medical aid debit order",
        "amount": Decimal("-2100.00"),
        "category": "Healthcare",
    },
]


def iso(day, hour):
    return f"2026-08-{day:02d}T{hour:02d}:00:00+00:00"


SESSIONS = [
    {
        "sessionId": "sess-demo-001",
        "userId": "demo-user-001",
        "customerId": "cust-001",
        "verified": True,
        "reviewStatus": "verified",
        "createdAt": iso(31, 8),
        "updatedAt": iso(31, 8),
        "messages": [
            {"role": "user", "text": "What is my balance?", "timestamp": iso(31, 8)},
            {
                "role": "assistant",
                "text": "I can help with your accounts and transactions.",
                "timestamp": iso(31, 8),
            },
        ],
    },
    {
        "sessionId": "sess-demo-002",
        "userId": "demo-user-002",
        "customerId": "cust-002",
        "verified": True,
        "reviewStatus": "verified",
        "createdAt": iso(30, 10),
        "updatedAt": iso(30, 10),
        "messages": [
            {"role": "user", "text": "Show me my recent transactions.", "timestamp": iso(30, 10)},
            {
                "role": "assistant",
                "text": "I will retrieve your recent transactions.",
                "timestamp": iso(30, 10),
            },
        ],
    },
    {
        "sessionId": "sess-demo-003",
        "userId": "demo-user-003",
        "customerId": "cust-003",
        "verified": True,
        "reviewStatus": "verified",
        "createdAt": iso(29, 14),
        "updatedAt": iso(29, 14),
        "messages": [
            {
                "role": "user",
                "text": "Can you show my spending by category?",
                "timestamp": iso(29, 14),
            },
            {
                "role": "assistant",
                "text": "I can prepare a spending summary for you.",
                "timestamp": iso(29, 14),
            },
        ],
    },
    {
        "sessionId": "sess-demo-004",
        "userId": "demo-user-004",
        "customerId": "cust-004",
        "verified": True,
        "reviewStatus": "verified",
        "createdAt": iso(28, 9),
        "updatedAt": iso(28, 9),
        "messages": [
            {"role": "user", "text": "What loan options are available?", "timestamp": iso(28, 9)},
            {
                "role": "assistant",
                "text": "I can explain the available loan products and requirements.",
                "timestamp": iso(28, 9),
            },
        ],
    },
    {
        "sessionId": "sess-demo-005",
        "userId": "demo-user-005",
        "customerId": "cust-005",
        "verified": True,
        "reviewStatus": "verified",
        "createdAt": iso(27, 11),
        "updatedAt": iso(27, 11),
        "messages": [
            {"role": "user", "text": "I need help opening an account.", "timestamp": iso(27, 11)},
            {
                "role": "assistant",
                "text": "I can guide you through the account-opening process.",
                "timestamp": iso(27, 11),
            },
        ],
    },
    {
        "sessionId": "sess-demo-006",
        "userId": "demo-user-006",
        "applicantName": "Lerato Mokoena",
        "applicantPhone": "0820001006",
        "applicantEmail": "lerato.mokoena@example.com",
        "reviewStatus": "pending_review",
        "createdAt": iso(26, 13),
        "updatedAt": iso(26, 13),
        "messages": [
            {"role": "user", "text": "I want to open a savings account.", "timestamp": iso(26, 13)},
            {
                "role": "assistant",
                "text": "Please provide your details and upload your required documents.",
                "timestamp": iso(26, 13),
            },
        ],
    },
]


CREDIT = [
    {
        "customerId": "cust-001",
        "sessionId": "credit-demo-001",
        "creditScore": 612,
        "rating": "fair",
        "lastUpdated": iso(31, 8),
    },
    {
        "customerId": "cust-002",
        "sessionId": "credit-demo-002",
        "creditScore": 705,
        "rating": "good",
        "lastUpdated": iso(31, 8),
    },
    {
        "customerId": "cust-003",
        "sessionId": "credit-demo-003",
        "creditScore": 640,
        "rating": "fair",
        "lastUpdated": iso(31, 8),
    },
    {
        "customerId": "cust-004",
        "sessionId": "credit-demo-004",
        "creditScore": 480,
        "rating": "poor",
        "lastUpdated": iso(31, 8),
    },
    {
        "customerId": "cust-005",
        "sessionId": "credit-demo-005",
        "creditScore": 585,
        "rating": "fair",
        "lastUpdated": iso(31, 8),
    },
    {
        "customerId": "cust-006",
        "sessionId": "credit-demo-006",
        "creditScore": 674,
        "rating": "good",
        "lastUpdated": iso(31, 8),
    },
]


APPLICATIONS = [
    {
        "reference": "APP-DEMO-001",
        "customerId": "cust-006",
        "accountType": "Savings",
        "status": "Pending",
        "applicantData": {
            "fullName": "Lerato Mokoena",
            "email": "lerato.mokoena@example.com",
            "phone": "0820001006",
        },
        "createdAt": iso(26, 13),
    },
]


DATASETS = {
    "customers": CUSTOMERS,
    "accounts": ACCOUNTS,
    "transactions": TRANSACTIONS,
    "sessions": SESSIONS,
    "bankers": BANKERS,
    "credit": CREDIT,
    "applications": APPLICATIONS,
}


RECOMMENDATION_FEATURES = {
    "age", "gender", "marital_status", "number_of_dependents", "education_level",
    "province", "employment_status", "monthly_income", "monthly_expenses",
    "account_tenure_years", "monthly_transactions", "avg_transaction_value",
    "digital_login_frequency", "branch_visit_count", "late_payment_count",
    "has_savings_account", "has_credit_card", "has_home_loan", "has_personal_loan",
    "has_vehicle_finance", "has_investment_account", "active_products_count",
    "has_mymo_account", "has_achieva_account", "has_prestige_banking",
    "has_private_banking", "has_signature_banking", "has_blue_credit_card",
    "has_gold_credit_card", "has_titanium_credit_card", "has_platinum_credit_card",
    "has_world_citizen_credit_card", "has_diners_club_platinum_credit_card",
    "has_term_loan", "has_revolving_loan", "has_overdraft",
    "has_investment_backed_lending", "has_student_loan", "has_fixed_deposit",
    "has_tax_free_savings", "credit_score", "loan_utilisation_rate",
    "days_since_last_default", "has_ever_defaulted", "savings_rate",
}


def decimalize(value):
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {key: decimalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decimalize(item) for item in value]
    return value


def validate():
    customer_ids = {item["customerId"] for item in CUSTOMERS}
    for item in CUSTOMERS:
        missing = sorted(RECOMMENDATION_FEATURES - item.keys())
        if missing:
            raise ValueError(f"{item['customerId']} is missing recommendation features: {missing}")
    account_ids = {item["accountId"] for item in ACCOUNTS}
    if len(customer_ids) < 6:
        raise ValueError("At least six customers are required")
    accounts_by_customer = {customer_id: 0 for customer_id in customer_ids}
    for item in ACCOUNTS:
        accounts_by_customer[item["customerId"]] += 1
    missing_accounts = [
        customer_id for customer_id, count in accounts_by_customer.items() if count == 0
    ]
    if missing_accounts:
        raise ValueError(f"Customers without accounts: {missing_accounts}")
    transactions_by_account = {account_id: 0 for account_id in account_ids}
    for item in TRANSACTIONS:
        transactions_by_account[item["accountId"]] += 1
    missing_transactions = [
        account_id for account_id, count in transactions_by_account.items() if count < 3
    ]
    if missing_transactions:
        raise ValueError(f"Accounts with fewer than three transactions: {missing_transactions}")


def write_dataset(dynamodb, dataset_name, items, dry_run):
    table_name = TABLE_NAMES[dataset_name]
    print(f"{dataset_name}: {len(items)} records -> {table_name}")
    if dry_run:
        return
    table = dynamodb.Table(table_name)
    with table.batch_writer(overwrite_by_pkeys=None) as batch:
        for item in items:
            batch.put_item(Item=decimalize(item))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--profile", help="AWS profile for the target account")
    parser.add_argument(
        "--table",
        action="append",
        choices=sorted(DATASETS),
        dest="tables",
        help="Seed only this dataset; repeat the option for multiple datasets",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Validate and print counts without writing"
    )
    args = parser.parse_args()

    validate()
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    dynamodb = session.resource("dynamodb")
    selected = args.tables or list(DATASETS)
    for dataset_name in selected:
        write_dataset(dynamodb, dataset_name, DATASETS[dataset_name], args.dry_run)
    print("Dry run complete." if args.dry_run else "Seeding complete.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit("Interrupted")
