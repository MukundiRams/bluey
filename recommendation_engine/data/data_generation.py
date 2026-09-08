"""
generate_bank_customers.py
==========================
Synthetic dataset generator for a South African bank customer segmentation project.

Pipeline overview (order matters — each layer conditions on the previous):
  1. Identifiers & Demographics   →  the "who"
  2. Financial Profile            →  driven by demographics
  3. Account & Behavioural        →  driven by financials + demographics
  4. Product Holdings             →  driven by financials + risk + behaviour
  5. Risk Indicators              →  driven by financials + products + behaviour
  6. Churn Risk Label             →  driven by all of the above

Dependencies:
    pip install numpy pandas
"""

import numpy as np
import pandas as pd

def generate_synthetic_bank_customers(N: int = 2000, OUTPUT_PATH: str = "bank_customers.csv"):
    # ── Reproducibility ────────────────────────────────────────────────────────────
    SEED = 42
    rng  = np.random.default_rng(SEED)

    N = 2000  # number of customers

    # ══════════════════════════════════════════════════════════════════════════════
    # SECTION 1 — IDENTIFIERS & DEMOGRAPHICS
    # Rationale: demographics are the root cause variables. Most financial and
    # behavioural features are downstream of age, education, and employment.
    # ══════════════════════════════════════════════════════════════════════════════

    # ── Customer IDs ───────────────────────────────────────────────────────────────
    customer_ids = [f"CUST-{str(i).zfill(5)}" for i in range(1, N + 1)]

    # ── South African first / last names ───────────────────────────────────────────
    # A curated pool covering the main language groups in SA
    # (Zulu, Xhosa, Sotho, Afrikaans, English, Indian-origin).
    MALE_FIRST = [
        "Sipho", "Thabo", "Bongani", "Lungelo", "Siyanda", "Tebogo", "Kagiso",
        "Lethiwe", "Nkosinathi", "Sandile", "Mandla", "Sbusiso", "Vusi", "Nhlanhla",
        "Musa", "Ruan", "Pieter", "Johan", "André", "Heinrich", "Rayan", "Priya",
        "Arjun", "Kiran", "Devan", "Luca", "Marco", "Dylan", "Kyle", "Jason",
    ]
    FEMALE_FIRST = [
        "Nomvula", "Zanele", "Lindiwe", "Nokwanda", "Thandi", "Ayanda", "Nozipho",
        "Palesa", "Kefilwe", "Lerato", "Bongiwe", "Siphokazi", "Nandi", "Mihlali",
        "Cleo", "Anika", "Liezel", "Chanel", "Priya", "Kavitha", "Shenay", "Fatima",
        "Zara", "Aaliya", "Tayla", "Jade", "Megan", "Ashleigh", "Kim", "Samantha",
    ]
    LAST_NAMES = [
        "Dlamini", "Nkosi", "Mthembu", "Ndlovu", "Zulu", "Khumalo", "Mkhize",
        "Nxumalo", "Cele", "Buthelezi", "Ngubane", "Mokoena", "Motsepe", "Sithole",
        "Mthethwa", "van der Berg", "Botha", "Pretorius", "du Plessis", "Joubert",
        "Pillay", "Naidoo", "Govender", "Reddy", "Maharaj", "Adams", "Hendricks",
        "Daniels", "Smith", "Johnson", "Brown", "Williams", "Jacobs", "February",
    ]

    # ── Age ────────────────────────────────────────────────────────────────────────
    # Skewed slightly towards 25-50 — the core economically active population.
    # Using a mixture of two normals to get realistic bimodality.
    age_young  = rng.normal(loc=32, scale=6,  size=N)
    age_older  = rng.normal(loc=50, scale=10, size=N)
    blend_mask = rng.random(N) < 0.65          # 65 % in the younger cluster
    ages       = np.where(blend_mask, age_young, age_older)
    ages       = np.clip(ages, 18, 75).astype(int)

    # ── Gender ─────────────────────────────────────────────────────────────────────
    genders = rng.choice(["Male", "Female"], size=N, p=[0.48, 0.52])

    first_names = [
        rng.choice(MALE_FIRST) if g == "Male" else rng.choice(FEMALE_FIRST)
        for g in genders
    ]
    last_names = [rng.choice(LAST_NAMES) for _ in range(N)]

    # ── Education ──────────────────────────────────────────────────────────────────
    # Rationale: education correlates strongly with income band. We model this
    # by assigning probabilities conditional on age (older cohorts more likely
    # to have lower formal education due to apartheid-era access disparities).
    education_levels = ["No Matric", "Matric", "Diploma", "Degree", "Postgraduate"]

    def sample_education(age):
        if age < 25:
            probs = [0.05, 0.35, 0.30, 0.25, 0.05]
        elif age < 40:
            probs = [0.08, 0.30, 0.28, 0.25, 0.09]
        elif age < 55:
            probs = [0.15, 0.35, 0.25, 0.18, 0.07]
        else:
            probs = [0.25, 0.38, 0.20, 0.12, 0.05]
        return rng.choice(education_levels, p=probs)

    education = np.array([sample_education(a) for a in ages])

    # ── Employment Status ──────────────────────────────────────────────────────────
    # Rationale: SA has high unemployment (~32%). Education and age shift the odds.
    employment_map = {
        "No Matric":    {"Employed": 0.35, "Self-Employed": 0.10, "Unemployed": 0.40, "Retired": 0.10, "Student": 0.05},
        "Matric":       {"Employed": 0.50, "Self-Employed": 0.12, "Unemployed": 0.28, "Retired": 0.07, "Student": 0.03},
        "Diploma":      {"Employed": 0.65, "Self-Employed": 0.15, "Unemployed": 0.12, "Retired": 0.05, "Student": 0.03},
        "Degree":       {"Employed": 0.72, "Self-Employed": 0.18, "Unemployed": 0.06, "Retired": 0.03, "Student": 0.01},
        "Postgraduate": {"Employed": 0.68, "Self-Employed": 0.25, "Unemployed": 0.03, "Retired": 0.03, "Student": 0.01},
    }

    def sample_employment(edu, age):
        probs_dict = employment_map[edu].copy()
        # Push older customers towards retirement
        if age >= 60:
            probs_dict["Retired"]   += 0.30
            probs_dict["Employed"]  -= 0.15
            probs_dict["Unemployed"] = max(0, probs_dict["Unemployed"] - 0.10)
            probs_dict["Student"]    = 0.0
        # Normalise so probs sum to 1
        total = sum(probs_dict.values())
        keys, vals = zip(*probs_dict.items())
        vals = [v / total for v in vals]
        return rng.choice(keys, p=vals)

    employment_status = np.array([sample_employment(e, a) for e, a in zip(education, ages)])

    # ── Marital Status & Dependents ────────────────────────────────────────────────
    # Rationale: marriage probability increases with age; dependents are correlated
    # with marital status and, later, with expenses.
    def sample_marital(age):
        if age < 25: p_married = 0.08
        elif age < 35: p_married = 0.40
        elif age < 50: p_married = 0.65
        else: p_married = 0.60        # divorces / widowhood reduce this slightly
        p_divorced = min(0.15, (age - 25) * 0.004) if age > 25 else 0.01
        p_widowed  = min(0.12, (age - 50) * 0.008) if age > 50 else 0.0
        p_single   = max(0.02, 1 - p_married - p_divorced - p_widowed)
        total = p_single + p_married + p_divorced + p_widowed
        return rng.choice(
            ["Single", "Married", "Divorced", "Widowed"],
            p=[p_single/total, p_married/total, p_divorced/total, p_widowed/total]
        )

    marital_status = np.array([sample_marital(a) for a in ages])

    def sample_dependents(marital, age):
        if marital == "Single":
            mu = 0.4
        elif marital == "Married":
            mu = 2.0 if age < 45 else 1.0
        elif marital == "Divorced":
            mu = 1.2
        else:  # Widowed
            mu = 0.8
        return int(np.clip(rng.poisson(mu), 0, 6))

    num_dependents = np.array([sample_dependents(m, a) for m, a in zip(marital_status, ages)])

    # ── Province ───────────────────────────────────────────────────────────────────
    # Rationale: weighted by actual SA province population sizes (2023 estimates).
    provinces = [
        "Gauteng", "KwaZulu-Natal", "Western Cape", "Eastern Cape",
        "Limpopo", "Mpumalanga", "North West", "Free State", "Northern Cape"
    ]
    province_probs = [0.26, 0.21, 0.12, 0.12, 0.10, 0.08, 0.06, 0.04, 0.01]
    province = rng.choice(provinces, size=N, p=province_probs)

    # ══════════════════════════════════════════════════════════════════════════════
    # SECTION 2 — FINANCIAL PROFILE
    # Rationale: income is the primary financial driver. It is conditioned on
    # education AND employment status — a postgrad who is unemployed earns nothing.
    # ══════════════════════════════════════════════════════════════════════════════

    # ── Monthly Income ─────────────────────────────────────────────────────────────
    # Base income distributions (ZAR) by education level (median, std).
    # SA median individual income is ~R8 500/month (StatsSA 2023).
    income_params = {
        "No Matric":    (5_000,  2_500),
        "Matric":       (8_500,  3_500),
        "Diploma":      (16_000, 6_000),
        "Degree":       (32_000, 12_000),
        "Postgraduate": (55_000, 20_000),
    }

    def sample_income(edu, emp, age):
        mu, sigma = income_params[edu]
        # Experience premium: income grows with age, peaks ~50, then declines slightly
        age_factor = 1 + 0.012 * (min(age, 50) - 25)
        base = rng.normal(loc=mu * age_factor, scale=sigma)
        # Employment modifier
        if emp == "Unemployed":
            return max(0, rng.normal(1_500, 800))   # grants / informal
        if emp == "Student":
            return max(0, rng.normal(3_000, 1_500))
        if emp == "Retired":
            return max(0, rng.normal(mu * 0.55, sigma * 0.4))
        if emp == "Self-Employed":
            base *= rng.uniform(0.7, 1.6)           # higher variance for self-employed
        return max(0, round(base, -2))              # round to nearest 100

    monthly_income = np.array([
        sample_income(e, em, a)
        for e, em, a in zip(education, employment_status, ages)
    ])

    # ── Income Band ────────────────────────────────────────────────────────────────
    income_band_cuts  = [0, 8_000, 20_000, 40_000, 80_000, np.inf]
    income_band_labels = ["LSM 1-4", "LSM 5-6", "LSM 7-8", "Affluent", "High Net Worth"]
    income_band = pd.cut(
        monthly_income, bins=income_band_cuts, labels=income_band_labels,
        include_lowest=True  # ensures income=0 falls into "LSM 1-4" not NaN
    )

    # ── Account Tenure ─────────────────────────────────────────────────────────────
    # Rationale: older customers have been banking longer. Capped at age - 16
    # (can't have had an account before ~16 years old).
    def sample_tenure(age, income):
        max_tenure = max(1, age - 16)
        # Wealthier customers are slightly more loyal / have banked longer
        mu = min(max_tenure * 0.5, 5 + income / 15_000)
        tenure = rng.normal(loc=mu, scale=3)
        return round(float(np.clip(tenure, 0.5, max_tenure)), 1)

    account_tenure_years = np.array([sample_tenure(a, i) for a, i in zip(ages, monthly_income)])

    # ── Monthly Expenses ───────────────────────────────────────────────────────────
    # Rationale: expenses scale with income but also with dependents.
    # A higher expense-to-income ratio is a risk indicator used later.
    def sample_expenses(income, dependents, marital):
        # base_ratio covers ALL living costs as a fraction of income (housing included).
        # Kept at 45-75% so the expense-to-income ratio is realistic, not pathological.
        base_ratio = rng.uniform(0.45, 0.75)
        dependent_uplift = dependents * rng.uniform(600, 1_200)   # per-dependent cost
        expenses = income * base_ratio + dependent_uplift
        # Married households share fixed costs → slight economies of scale
        if marital == "Married":
            expenses *= rng.uniform(0.85, 0.95)
        return round(float(np.clip(expenses, 300, income * 1.05)), -2)

    monthly_expenses = np.array([
        sample_expenses(i, d, m)
        for i, d, m in zip(monthly_income, num_dependents, marital_status)
    ])

    # Derived: expense-to-income ratio (used internally for risk scoring)
    expense_ratio = monthly_expenses / np.where(monthly_income < 500, 500, monthly_income)

    # ══════════════════════════════════════════════════════════════════════════════
    # SECTION 3 — BEHAVIOURAL FEATURES
    # Rationale: digital engagement is higher in younger, urban, higher-income
    # customers. Branch visits are inversely related to digital engagement.
    # Transaction patterns reflect spending volume (linked to income).
    # ══════════════════════════════════════════════════════════════════════════════

    # ── Digital Engagement Score (latent variable) ─────────────────────────────────
    # We create a hidden "digital affinity" score that drives login frequency
    # and partially influences branch visits.
    digital_affinity = (
        (75 - ages) / 60 * 0.4              # younger → more digital
        + (monthly_income / 80_000) * 0.3   # richer → more digital
        + (province == "Gauteng").astype(float) * 0.1
        + (province == "Western Cape").astype(float) * 0.08
        + rng.normal(0, 0.15, N)
    )
    digital_affinity = np.clip(digital_affinity, 0, 1)

    # ── Digital Login Frequency (logins per month) ─────────────────────────────────
    digital_login_frequency = np.round(
        np.clip(rng.normal(loc=digital_affinity * 25 + 5, scale=5), 0, 60)
    ).astype(int)

    # ── Branch Visit Count (visits per month) ─────────────────────────────────────
    # Inversely related to digital engagement but older + rural customers visit more.
    branch_mean = (1 - digital_affinity) * 4 + (ages / 75) * 2
    branch_visit_count = np.round(
        np.clip(rng.poisson(lam=np.clip(branch_mean, 0.1, 8)), 0, 15)
    ).astype(int)

    # ── Monthly Transactions & Average Transaction Value ──────────────────────────
    # More transactions for higher-income customers; avg value also scales with income.
    monthly_transactions = np.round(
        np.clip(rng.normal(loc=monthly_income / 1_500 + 10, scale=8), 3, 120)
    ).astype(int)

    avg_transaction_value = np.round(
        np.clip(
            rng.normal(loc=monthly_expenses / np.clip(monthly_transactions, 1, 120), scale=200),
            50, 15_000
        ), 2
    )

    # ── Late Payment Count (last 12 months) ────────────────────────────────────────
    # Rationale: late payments are more frequent among customers with high
    # expense ratios and low income. Key risk indicator.
    late_payment_prob = np.clip(expense_ratio * 0.4 - 0.1 + rng.normal(0, 0.1, N), 0, 0.9)
    late_payment_count = rng.binomial(n=12, p=late_payment_prob).astype(int)

    # ══════════════════════════════════════════════════════════════════════════════
    # SECTION 4 — PRODUCT HOLDINGS
    # Rationale: product uptake follows a rough "financial maturity" ladder.
    # A savings account is almost universal. Credit cards and home loans
    # require a decent income and credit standing. Investments are for
    # the affluent. Each product is modelled with income-band probabilities,
    # then adjusted by age and credit score (calculated in the next section but
    # we need a proxy here — we use income as a proxy).
    # ══════════════════════════════════════════════════════════════════════════════

    # Income-based probabilities for each product
    def product_probs(income):
        """Return P(has_product) for each product given monthly income."""
        i = income
        return {
            "savings_account":    min(0.98, 0.60 + i / 30_000),
            "credit_card":        np.clip(0.10 + i / 35_000, 0, 0.92),
            "home_loan":          np.clip(-0.10 + i / 45_000, 0, 0.70),
            "personal_loan":      np.clip(0.15 + i / 60_000, 0, 0.55),
            "vehicle_finance":    np.clip(-0.05 + i / 40_000, 0, 0.65),
            "investment_account": np.clip(-0.20 + i / 55_000, 0, 0.75),
        }

    has_savings_account    = np.zeros(N, dtype=int)
    has_credit_card        = np.zeros(N, dtype=int)
    has_home_loan          = np.zeros(N, dtype=int)
    has_personal_loan      = np.zeros(N, dtype=int)
    has_vehicle_finance    = np.zeros(N, dtype=int)
    has_investment_account = np.zeros(N, dtype=int)

    catalogue_columns = [
        "has_mymo_account", "has_achieva_account", "has_prestige_banking",
        "has_private_banking", "has_signature_banking", "has_blue_credit_card",
        "has_gold_credit_card", "has_titanium_credit_card",
        "has_platinum_credit_card", "has_world_citizen_credit_card",
        "has_diners_club_platinum_credit_card", "has_term_loan",
        "has_revolving_loan", "has_overdraft", "has_investment_backed_lending",
        "has_student_loan", "has_fixed_deposit", "has_tax_free_savings",
    ]
    catalogue_holdings = {
        column: np.zeros(N, dtype=int) for column in catalogue_columns
    }

    for idx in range(N):
        pp = product_probs(monthly_income[idx])

        # Age modifiers — home loans and vehicle finance peak in 30s-50s
        age_i = ages[idx]
        home_loan_age_factor   = 1.3 if 28 <= age_i <= 55 else 0.6
        vehicle_age_factor     = 1.2 if 25 <= age_i <= 50 else 0.7
        investment_age_factor  = 1.4 if age_i >= 40 else 0.7
        credit_card_age_factor = 0.5 if age_i < 22 else 1.0

        has_savings_account[idx]    = rng.random() < pp["savings_account"]
        has_credit_card[idx]        = rng.random() < np.clip(pp["credit_card"] * credit_card_age_factor, 0, 1)
        has_home_loan[idx]          = rng.random() < np.clip(pp["home_loan"] * home_loan_age_factor, 0, 1)
        has_personal_loan[idx]      = rng.random() < pp["personal_loan"]
        has_vehicle_finance[idx]    = rng.random() < np.clip(pp["vehicle_finance"] * vehicle_age_factor, 0, 1)
        has_investment_account[idx] = rng.random() < np.clip(pp["investment_account"] * investment_age_factor, 0, 1)

        income = monthly_income[idx]
        credit_proxy = np.clip(500 + income / 1_000 * 2 - late_payment_count[idx] * 8, 350, 800)

        # Account tiers are mutually exclusive and rise with income.
        if income >= 92_000 and rng.random() < 0.35:
            catalogue_holdings["has_signature_banking"][idx] = 1
        elif income >= 58_000 and rng.random() < 0.45:
            catalogue_holdings["has_private_banking"][idx] = 1
        elif income >= 25_000 and rng.random() < 0.55:
            catalogue_holdings["has_prestige_banking"][idx] = 1
        elif income >= 8_000 and rng.random() < 0.65:
            catalogue_holdings["has_achieva_account"][idx] = 1
        else:
            catalogue_holdings["has_mymo_account"][idx] = 1

        # Card tiers represent the product held, rather than duplicating the
        # aggregate credit-card feature used by the clustering model.
        if has_credit_card[idx]:
            if income >= 92_000 and credit_proxy >= 650:
                card = "has_world_citizen_credit_card"
            elif income >= 58_000 and credit_proxy >= 650:
                card = "has_platinum_credit_card"
            elif income >= 25_000 and credit_proxy >= 600:
                card = "has_titanium_credit_card"
            elif income >= 5_000 and credit_proxy >= 580:
                card = "has_gold_credit_card"
            else:
                card = "has_blue_credit_card"
            catalogue_holdings[card][idx] = 1
            catalogue_holdings["has_diners_club_platinum_credit_card"][idx] = int(
                income >= 41_000 and credit_proxy >= 620 and rng.random() < 0.08
            )

        catalogue_holdings["has_term_loan"][idx] = int(
            has_personal_loan[idx] and rng.random() < 0.55
        )
        catalogue_holdings["has_revolving_loan"][idx] = int(
            has_personal_loan[idx] and income >= 8_000 and credit_proxy >= 580
            and rng.random() < 0.35
        )
        catalogue_holdings["has_overdraft"][idx] = int(
            income >= 8_000 and credit_proxy >= 580 and rng.random() < 0.25
        )
        catalogue_holdings["has_investment_backed_lending"][idx] = int(
            has_investment_account[idx] and income >= 50_000
            and rng.random() < 0.20
        )
        catalogue_holdings["has_student_loan"][idx] = int(
            employment_status[idx] == "Student" and rng.random() < 0.30
        )
        catalogue_holdings["has_fixed_deposit"][idx] = int(
            has_investment_account[idx] and income >= 10_000 and rng.random() < 0.30
        )
        catalogue_holdings["has_tax_free_savings"][idx] = int(
            has_investment_account[idx] and income >= 10_000 and rng.random() < 0.25
        )

        # Business rule: can't have a home loan without a savings account as entry point
        if has_home_loan[idx] and not has_savings_account[idx]:
            has_savings_account[idx] = 1

    active_products_count = (
        has_savings_account + has_credit_card + has_home_loan
        + has_personal_loan + has_vehicle_finance + has_investment_account
    )

    # ══════════════════════════════════════════════════════════════════════════════
    # SECTION 5 — RISK INDICATORS
    # Rationale: credit score is the canonical risk summary. We build it from
    # income, payment history (late payments), loan obligations, and tenure.
    # Loan utilisation is how much of credit limits are drawn down.
    # ══════════════════════════════════════════════════════════════════════════════

    # ── Credit Score (300–850 range, SA-style) ─────────────────────────────────────
    # Components and their weights:
    #   +  income              (wealth signal)
    #   +  tenure              (loyalty / stability)
    #   -  late payments       (payment history — largest negative driver)
    #   +  active products     (breadth of relationship, positive if managed well)
    #   +  age                 (experience with credit, up to ~50)
    def compute_credit_score(income, tenure, late_pay, products, age):
        base  = 580
        base += np.clip(income / 1_500, 0, 120)          # up to +120 for high earners
        base += tenure * 5                                # +5 per year of tenure
        base -= late_pay * 35                             # -35 per late payment
        base += products * 8                              # small multi-product bonus
        base += np.clip((age - 25) * 1.5, -20, 40)       # experience premium
        noise = rng.normal(0, 25)
        return int(np.clip(base + noise, 300, 850))

    credit_score = np.array([
        compute_credit_score(i, t, lp, p, a)
        for i, t, lp, p, a in zip(
            monthly_income, account_tenure_years,
            late_payment_count, active_products_count, ages
        )
    ])

    # ── Loan Utilisation Rate ──────────────────────────────────────────────────────
    # Rationale: customers with loans and high expenses relative to income
    # tend to draw down more of their credit. Low credit scores → higher utilisation.
    def compute_loan_util(has_cc, has_hl, has_pl, has_vf, credit_sc, expense_rat):
        if has_cc + has_hl + has_pl + has_vf == 0:
            return 0.0                              # no credit facilities → 0%
        # Higher expense ratio and lower credit score → higher utilisation
        base_util = (1 - (credit_sc - 300) / 550) * 0.6 + expense_rat * 0.3
        noise = rng.normal(0, 0.08)
        return round(float(np.clip(base_util + noise, 0.0, 1.0)), 3)

    loan_utilisation_rate = np.array([
        compute_loan_util(cc, hl, pl, vf, cs, er)
        for cc, hl, pl, vf, cs, er in zip(
            has_credit_card, has_home_loan, has_personal_loan,
            has_vehicle_finance, credit_score, expense_ratio
        )
    ])

    # ── Days Since Last Default ────────────────────────────────────────────────────
    # Rationale: customers who have never defaulted get a high value (e.g., 9999).
    # Recent defaulters have lower credit scores, more late payments.
    # Probability of ever having defaulted is inversely related to credit score.
    def sample_days_since_default(credit_sc, late_pay):
        p_default_history = np.clip(1 - (credit_sc - 300) / 600 + late_pay * 0.05, 0, 0.85)
        has_defaulted = rng.random() < p_default_history
        if not has_defaulted:
            return 9999                             # sentinel: never defaulted
        # More recent default for lower scores
        mu_days = (credit_sc - 300) / 550 * 1800 + 30
        return int(np.clip(rng.exponential(scale=mu_days), 10, 3650))

    days_since_last_default = np.array([
        sample_days_since_default(cs, lp)
        for cs, lp in zip(credit_score, late_payment_count)
    ])

    # ── Preferred Channel ──────────────────────────────────────────────────────────
    # Extra feature: inferred from behaviour (not directly used in clustering,
    # but useful for personalisation downstream).
    def preferred_channel(digital_logins, branch_visits):
        if digital_logins > 20 and branch_visits <= 1:
            return "Digital-Only"
        elif digital_logins > 10:
            return "Digital-Preferred"
        elif branch_visits > 3:
            return "Branch-Preferred"
        else:
            return "Passive"

    preferred_channel_col = np.array([
        preferred_channel(d, b)
        for d, b in zip(digital_login_frequency, branch_visit_count)
    ])

    # ══════════════════════════════════════════════════════════════════════════════
    # SECTION 6 — CHURN RISK LABEL
    # Rationale: churn is a multi-factor outcome. We build a continuous
    # "churn score" from the key drivers, then bin it into Low / Medium / High.
    #
    # High churn risk drivers:
    #   - Few products (low relationship depth)
    #   - Low credit score (often correlates with financial stress)
    #   - High loan utilisation
    #   - Many late payments
    #   - Low digital engagement (disengaged customers)
    #   - Low tenure (haven't built loyalty yet)
    #   - High expense-to-income ratio (financial pressure)
    #
    # Low churn risk drivers:
    #   - Many products (switching costs are high)
    #   - Long tenure
    #   - High income
    #   - Engaged digitally
    # ══════════════════════════════════════════════════════════════════════════════

    def churn_score(products, credit_sc, loan_util, late_pay,
                    digital_logins, tenure, expense_rat, income):
        score = 0.0
        score += (6 - products) * 0.15             # fewer products → higher churn
        score += (850 - credit_sc) / 550 * 0.20   # lower credit score → higher
        score += loan_util * 0.20                  # high utilisation → financial stress
        score += late_pay / 12 * 0.20              # late payments → disengagement
        score += (1 - digital_logins / 60) * 0.10 # low digital → disengaged
        score += (1 - min(tenure, 15) / 15) * 0.10  # short tenure → not loyal
        score += np.clip(expense_rat - 0.7, 0, 0.5) * 0.05  # expense pressure
        noise = rng.normal(0, 0.06)
        return float(np.clip(score + noise, 0, 1))

    churn_scores = np.array([
        churn_score(p, cs, lu, lp, dl, t, er, i)
        for p, cs, lu, lp, dl, t, er, i in zip(
            active_products_count, credit_score, loan_utilisation_rate,
            late_payment_count, digital_login_frequency,
            account_tenure_years, expense_ratio, monthly_income
        )
    ])

    # Add tiny jitter to break ties at the clipping boundary (score=1.0),
    # then use pd.qcut to guarantee the intended distribution.
    churn_scores_jittered = churn_scores + rng.uniform(-1e-6, 1e-6, N)
    churn_risk_label = pd.qcut(
        churn_scores_jittered,
        q=[0, 0.50, 0.80, 1.0],
        labels=["Low", "Medium", "High"],
        duplicates="drop"
    )

    # ══════════════════════════════════════════════════════════════════════════════
    # SECTION 7 — ASSEMBLE DATAFRAME
    # ══════════════════════════════════════════════════════════════════════════════

    df = pd.DataFrame({
        # Identifiers
        "customer_id":              customer_ids,
        "first_name":               first_names,
        "last_name":                last_names,

        # Demographics
        "age":                      ages,
        "gender":                   genders,
        "marital_status":           marital_status,
        "number_of_dependents":     num_dependents,
        "education_level":          education,
        "province":                 province,
        "employment_status":        employment_status,

        # Financial
        "monthly_income":           monthly_income.round(2),
        "income_band":              income_band,
        "monthly_expenses":         monthly_expenses.round(2),
        "account_tenure_years":     account_tenure_years,

        # Behavioural
        "monthly_transactions":     monthly_transactions,
        "avg_transaction_value":    avg_transaction_value,
        "digital_login_frequency":  digital_login_frequency,
        "branch_visit_count":       branch_visit_count,
        "late_payment_count":       late_payment_count,
        "preferred_channel":        preferred_channel_col,  # derived extra feature

        # Products
        "has_savings_account":      has_savings_account,
        "has_credit_card":          has_credit_card,
        "has_home_loan":            has_home_loan,
        "has_personal_loan":        has_personal_loan,
        "has_vehicle_finance":      has_vehicle_finance,
        "has_investment_account":   has_investment_account,
        "active_products_count":    active_products_count,
        **catalogue_holdings,

        # Risk
        "credit_score":             credit_score,
        "loan_utilisation_rate":    loan_utilisation_rate,
        "days_since_last_default":  days_since_last_default,

        # Target
        "churn_risk_label":         churn_risk_label,
    })

    # ── Quick sanity-check prints ──────────────────────────────────────────────────
    # print("=" * 60)
    # print(f"Dataset shape: {df.shape}")
    # print("\nChurn risk distribution:")
    # print(df["churn_risk_label"].value_counts(normalize=True).mul(100).round(1).astype(str) + "%")
    # print("\nIncome band distribution:")
    # print(df["income_band"].value_counts(normalize=True).mul(100).round(1).astype(str) + "%")
    # print("\nMissing values:")
    # print(df.isnull().sum()[df.isnull().sum() > 0] if df.isnull().sum().any() else "  None")
    # print("\nSample rows:")
    # print(df[["customer_id", "first_name", "last_name", "age", "monthly_income",
    #           "income_band", "credit_score", "active_products_count",
    #           "churn_risk_label"]].head(10).to_string(index=False))
    # print("=" * 60)

    # ── Save to CSV ────────────────────────────────────────────────────────────────

    df.to_csv(OUTPUT_PATH, index=False)
    print(f"\n✓ Dataset saved to '{OUTPUT_PATH}'")
