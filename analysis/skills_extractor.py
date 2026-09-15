"""
Deterministic, free skill/tech-stack extraction from job description text.

Design: flat keyword matching against a curated list, not NLP/LLM-based. This
is intentional — see docs/context.md for why (structure-agnostic, instant,
free, and good enough for a "what's trending" dashboard). The list below is a
hand-curated starting point covering the four role categories this project
scrapes for (machine learning, AI engineer, AI scientist, product manager).
It's a plain Python data structure — edit it directly any time you notice a
term missing. See the "corpus mining" idea in the project notes for how to
grow this later from real scraped postings instead of guessing.
"""

import re

# Each entry maps a canonical display name -> list of text variants to match
# (so "Postgres" and "PostgreSQL" both count as the same skill). Matching is
# case-insensitive with word-boundary regex, so short/ambiguous names (R, Go)
# carry some false-positive risk against normal English prose — acceptable
# for a "roughly what's trending" dashboard, not meant to be exact.
SKILL_TAXONOMY: dict[str, list[str]] = {
    # --- Programming languages ---
    "Python": ["python"],
    "SQL": [r"\bsql\b"],
    # \b on "r programming" is load-bearing: without it the variant matches
    # inside "pair programming" (255 of 1,360 R tags in the corpus came from
    # that alone). The bare \br\b still carries real "Python, R, SQL" mentions.
    # Bare \br\b also fired on "R&D" and on requisition ids like "Job ID:
    # R-102832" — 488 JDs (2026-09-15 taxonomy refresh).
    "R": [r"\br programming\b", r"\br\b(?!\s*&\s*d\b)(?!-\d)"],
    "Java": ["java(?!script)"],
    "C#": [r"\bc#", "c-sharp"],
    "JavaScript": ["javascript", r"\bjs\b"],
    "TypeScript": ["typescript"],
    "C++": [r"c\+\+"],
    "Scala": [r"\bscala\b"],
    "Julia": ["julia"],

    # --- ML / DL frameworks ---
    "PyTorch": ["pytorch", r"\btorch\b"],
    "TensorFlow": ["tensorflow", r"\btf\b"],
    "Keras": ["keras"],
    "scikit-learn": ["scikit-learn", "sklearn"],
    "XGBoost": ["xgboost"],
    "LightGBM": ["lightgbm"],
    "JAX": [r"\bjax\b"],
    "Hugging Face": ["hugging face", "huggingface"],
    "ONNX": ["onnx"],
    "Deep Learning": ["deep learning"],
    "CNN": [r"\bcnns?\b", "convolutional neural network"],
    "RNN": [r"\brnns?\b", "recurrent neural network"],
    "LSTM": [r"\blstms?\b"],

    # --- LLM / GenAI specific ---
    "LLM": [r"\bllms?\b", "large language model"],
    "Prompt Engineering": ["prompt engineering"],
    "RAG": [r"\brag\b", "retrieval augmented generation", "retrieval-augmented generation"],
    "LangChain": ["langchain"],
    "LlamaIndex": ["llamaindex", "llama index"],
    # Space and verb forms added 2026-09-15 (+100 JDs): "fine tuning",
    # "finetune open-source models". Bare "fine-tune" is left out on purpose —
    # it is ordinary business English ("fine-tune solutions for customers").
    "Fine-tuning": [
        r"fine[- ]?tuning",
        r"fine[- ]?tun(?:e|ed|es)\s+(?:[\w.-]+\s+){0,2}(?:models?|llms?|language models?|transformers?|bert|embeddings?|slms?)",
    ],
    "Vector Database": ["vector database", "vector db", "vector store"],
    "OpenAI API": ["openai api", "gpt-4", "gpt4"],
    "Anthropic API": ["anthropic api", "claude api"],
    "Agents": ["ai agent", "agentic"],
    "Transformers": ["transformer model", "transformers library"],
    "Generative AI": ["generative ai", r"\bgenai\b", "gen ai"],
    # Merged from a separate "Agent Orchestration" entry — same underlying
    # competency (coordinating multiple agents/tools), was double-counting
    # one requirement as 2 skills (confirmed on real postings that matched
    # both "Multi-Agent Systems" and "Agent Orchestration" for one
    # multi-agent JD requirement). "Agents" stays separate — that's a
    # different granularity (any agent work, including single-agent).
    "Multi-Agent Systems": [
        "multi-agent", "multiagent", "multi agent",
        "agent orchestration", "orchestration framework",
    ],
    "Tool Use / Function Calling": ["function calling", "tool calling", "tool use"],
    "AutoGen": [r"\bautogen\b"],
    "CrewAI": ["crewai", "crew ai"],
    "LangGraph": ["langgraph"],
    "MCP": [r"\bmcp\b", "model context protocol"],
    "Semantic Kernel": ["semantic kernel"],
    "Claude Agent SDK": ["claude agent sdk"],
    "A2A Protocol": [r"\ba2a\b", "agent-to-agent", "agent to agent"],
    "Agent Skills": ["agent skills"],
    "Copilot": [r"\bcopilots?\b"],
    # Singular "embedding" is usually the verb ("embedding AI into workflows",
    # "by embedding these principles") — 163 JDs were tagged on that alone
    # (2026-09-15). Plural, compound and list-position forms carry the skill.
    "Embeddings": [
        r"\bembeddings\b",
        r"embedding[- ](?:models?|vectors?|spaces?|generation|search|based|pipelines?|systems?|techniques?|approach(?:es)?|strateg(?:y|ies))",
        r"(?:vector|text|word|semantic|sentence|image) embeddings?",
        r"chunking(?:,| and| &)\s+embedding",
        r"embedding and (?:ingestion|retrieval)",
        r"(?:retrieval|ingestion) and embedding",
        r"\bembedding\b(?=\s*[,/&)])",
        r"(?<=, )embedding\b",
    ],
    "Semantic Search": ["semantic search"],

    # --- Enterprise LLM platforms ---
    "Gemini": [r"\bgemini\b"],
    "AWS Bedrock": ["aws bedrock", "amazon bedrock", r"\bbedrock\b"],
    "Vertex AI": ["vertex ai"],
    "Azure OpenAI": ["azure openai"],

    # --- AI evaluation & safety ---
    # Merged from a separate "AI Evals" entry — same underlying competency,
    # was being double-counted as 2 skills for one JD requirement (e.g. a
    # job titled "AI Evals" itself listed both as missing). "ai evals" is
    # kept as a variant for coverage, but real postings say "evaluation
    # framework"/"model evaluation" ~75x more often (151/128 vs 2 in the
    # corpus), so LLM Evaluation is the canonical name, not AI Evals.
    "LLM Evaluation": [
        "llm evaluation", "model evaluation", "ai evals",
        r"\bevals?\b", "evaluation harness",
        r"eval(?:uation)?\s+(?:framework|pipeline|suite|metrics?)",
        "automated evaluation", "human evaluation", "golden dataset",
    ],
    "LLM-as-a-Judge": ["llm-as-a-judge", "llm as a judge"],
    "Red-teaming": ["red-teaming", "red teaming"],
    "Hallucination Detection": ["hallucination"],
    "Observability": ["observability"],
    "Responsible AI": ["responsible ai"],
    "Human-in-the-Loop": ["human-in-the-loop", "human in the loop"],
    "PII": [r"\bpii\b", "personally identifiable information"],
    # Added 2026-09-15 (LLM discovery over 500 ml_ai JDs; corpus: 475 JDs).
    "HIPAA": [r"\bhipaa\b"],
    "Differential Privacy": ["differential privacy"],
    "LangSmith": ["langsmith"],
    "Ragas": [r"\bragas\b"],
    "DeepEval": ["deepeval"],
    "Arize": [r"\barize\b"],

    # --- Computer vision / NLP ---
    # Bare "CV" is almost always a resume ("upload your CV", "Resume/CV") —
    # 156 JDs (2026-09-15). Only CV in an ML context counts now.
    "Computer Vision": [
        "computer vision",
        r"\bcv (?:models?|pipelines?|systems?|engineer(?:ing)?|research|tasks?)\b",
        r"\b(?:ml|nlp|ai)\s*/\s*cv\b",
        r"\bcv\s*/\s*(?:ml|nlp)\b",
    ],
    "NLP": ["nlp", "natural language processing"],
    "OpenCV": ["opencv"],
    "Scikit-image": ["scikit-image", "skimage"],
    "Speech Recognition": ["speech recognition", "asr"],
    "NLTK": [r"\bnltk\b"],
    "SpaCy": [r"\bspacy\b"],
    "Gensim": [r"\bgensim\b"],
    "LSA": [r"\blsa\b", "latent semantic analysis"],
    "BERT": [r"\bbert\b"],

    # --- Data science / analysis ---
    "Pandas": ["pandas"],
    "NumPy": ["numpy"],
    "SciPy": ["scipy"],
    "Matplotlib": ["matplotlib"],
    "Seaborn": ["seaborn"],
    "Statistics": ["statistics", "statistical modeling"],
    "Hypothesis Testing": ["hypothesis testing"],
    "Causal Inference": ["causal inference"],
    "Uncertainty Quantification": ["uncertainty quantification"],
    # \b on "ab test": the bare variant matched "lab testing" in 11 JDs.
    "A/B Testing": ["a/b test", r"\bab test", "experimentation"],
    "Data Visualization": ["data visualization", "tableau", "power bi"],
    "Jupyter": ["jupyter"],

    # --- Feature engineering / dimensionality reduction ---
    "PCA": [r"\bpca\b", "principal component analysis"],
    "NMF": [r"\bnmf\b", "non-negative matrix factorization", "nonnegative matrix factorization"],

    # --- Classical ML models & techniques ---
    "ARIMA": [r"\barima\b"],
    "SARIMA": [r"\bsarima\b"],
    "SVM": [r"\bsvm\b", "support vector machine"],
    "LDA": [r"\blda\b", "linear discriminant analysis", "latent dirichlet allocation"],
    "Random Forest": ["random forest"],
    "Gradient Boosting": ["gradient boost", "gradient boosting", r"\bgbm\b"],
    "K-means": ["k-means", "kmeans", r"\bk means\b"],
    "KNN": [r"\bknn\b", "k-nearest neighbor", "k nearest neighbor"],
    "DBSCAN": [r"\bdbscan\b"],
    "Hierarchical Clustering": ["hierarchical clustering"],
    "Clustering": [r"\bclustering\b"],
    "Time Series Forecasting": ["time series forecasting", "time series analysis", "time-series"],
    "Reinforcement Learning": [r"\brl\b", "reinforcement learning"],
    "SHAP": [r"\bshap\b"],
    "Recommendation Systems": [
        # Bare "recommendation(s)" / "recommending" used to count, which tagged
        # 1,365 JDs on ordinary English ("prioritized recommendations",
        # "technical recommendation report") — 2026-09-15 refresh. Now only
        # recommender-system phrasing; "recommender(s)" alone stays, it is
        # never ordinary English.
        r"recommend(?:ation|er)[- ](?:system|engine|model|algorithm)s?",
        r"\brecommenders?\b",
        r"personali[sz]ed recommendations?",
    ],
    "Collaborative Filtering": ["collaborative filtering"],

    # --- Data engineering / big data ---
    # PySpark merged in — it's Spark's own Python API, not a different
    # technology, same class of bug as the AI Evals/LLM Evaluation and
    # Agent Orchestration/Multi-Agent Systems merges above.
    "Spark": [
        "apache spark",
        r"\bpyspark\b",
        # Excludes the verb — "spark innovation", "find your spark" — 44 JDs (2026-09-15).
        r"(?<!your )(?<!the )(?<!a )(?<!to )\bspark\b(?!\s+(?:innovation|ideas?|new\b|curiosity|creativity|joy|change|conversations?|interest|excitement|meaningful|growth|and grow|of\b))",
    ],
    "Hadoop": ["hadoop"],
    "Kafka": ["kafka"],
    "Airflow": ["airflow"],
    "ETL": [r"\betl\b", r"\belt\b"],
    "dbt": [r"\bdbt\b"],
    "Snowflake": ["snowflake"],
    "Databricks": ["databricks"],
    "BigQuery": ["bigquery"],
    # Added 2026-09-15 (LLM discovery; corpus: 2,210 / 795 JDs).
    "Data Pipelines": [r"data (?:processing |ingestion )?pipelines?"],
    "Data Modeling": [r"data model(?:l)?ing", r"dimensional model(?:l)?ing"],

    # --- Databases ---
    "PostgreSQL": ["postgresql", "postgres"],
    "MySQL": ["mysql"],
    "MongoDB": ["mongodb", "mongo"],
    "Redis": ["redis"],
    "Elasticsearch": ["elasticsearch"],
    "DynamoDB": ["dynamodb"],

    # --- Cloud / infra ---
    "AWS": [r"\baws\b", "amazon web services"],
    "GCP": ["gcp", "google cloud"],
    "Azure": ["azure"],
    "Docker": ["docker"],
    "Kubernetes": ["kubernetes", r"\bk8s\b"],
    "Terraform": ["terraform"],
    "CI/CD": ["ci/cd", "continuous integration", "continuous deployment"],
    "Linux": [r"\blinux\b"],
    "Flask": [r"\bflask\b"],
    "Streamlit": ["streamlit"],
    "NoSQL": [r"\bnosql\b"],
    "Microservices": ["microservices", "microservice"],
    "REST API": ["rest api", r"\brestful\b"],
    "gRPC": [r"\bgrpc\b"],
    "Event-Driven Architecture": ["event-driven", "event driven"],

    # --- Software engineering ---
    # Added 2026-09-15 (LLM discovery; corpus: 1,538 / 773 / 996 JDs).
    "Distributed Systems": [r"distributed systems?", "distributed computing"],
    "API Design & Development": [
        "api design", "api development",
        r"designing (?:and building )?apis",
        r"build(?:ing)? (?:scalable |robust |restful )?apis",
    ],
    "Software Testing": [
        r"unit[- ]test(?:s|ing)?", r"integration[- ]test(?:s|ing)?",
        r"end[- ]to[- ]end test(?:s|ing)?", r"automated test(?:s|ing)?", "test automation",
    ],

    # --- AI coding tools ---
    # Added 2026-09-15 (corpus: 734 / 647 / 329 JDs). Copilot stays under
    # Agentic AI: most of its matches are product "copilots", not GitHub Copilot.
    "Claude Code": ["claude code"],
    "Cursor": [r"\bcursor\b"],
    "Codex": [r"\bcodex\b"],

    # --- Web scraping ---
    "Beautiful Soup": ["beautiful soup", "beautifulsoup", r"\bbs4\b"],
    "Selenium": [r"\bselenium\b"],

    # --- MLOps ---
    "MLOps": ["mlops"],
    "MLflow": ["mlflow"],
    "Model Monitoring": ["model monitoring", "model drift"],
    "Feature Store": ["feature store"],

    # --- Version control / collaboration ---
    "Git": [r"\bgit\b", "github", "gitlab"],
    "Jira": ["jira"],
    "Confluence": ["confluence"],

    # --- Product management ---
    # Tightened from a bare "roadmap" match, which over-triggered on ml_ai
    # JDs that mention "technical roadmap" once in passing (296 of 856
    # matches were ml_ai-track, not the PM roles this skill is meant for).
    # Now requires ownership language, not just any mention of the word.
    "Roadmapping": [
        "roadmapping", "roadmap ownership",
        r"(?:own|drive|define|set|build|shape)(?:s|ed|ing)?\s+(?:the\s+|a\s+|our\s+)?(?:product\s+|technical\s+)?roadmap",
    ],
    "User Research": ["user research", "user interviews"],
    "Agile": [r"\bagile\b"],  # \b: bare "agile" matched "fragile" in 38 JDs
    "Scrum": ["scrum"],
    "Product Analytics": ["product analytics", "amplitude", "mixpanel"],
    "SQL Analytics": ["sql querying"],
    "Stakeholder Management": ["stakeholder management"],
    "Go-to-Market": ["go-to-market", "gtm strategy"],
    "PRD Writing": ["prd", "product requirements document"],
    "Wireframing": ["wireframe", "figma"],
    "Customer Discovery": ["customer discovery"],
    "OKRs": ["okrs", "okr"],

    # --- Education / degree signals (not skills per se, but useful tags) ---
    "PhD": [r"\bph\.?d\.?\b"],
    # Unbounded "msc" matched glued capture text ("systemsCollaborate") and
    # names like MSCI — 118 JDs (2026-09-15).
    "Master's Degree": ["master's degree", "ms degree", r"\bmsc\b"],
}

# Groups skill names into themes, e.g. "how many jobs mention ANYTHING in the
# Agentic AI theme" — needed because a theme-level stat can't just sum each
# skill's individual percentage (a job matching both "LangGraph" and "MCP"
# would get double-counted). Query with `WHERE skill_name IN (skills_in_category(...))`
# to correctly count each job once even if it matched multiple tags in the theme.
SKILL_CATEGORIES: dict[str, list[str]] = {
    "Programming Languages": [
        "Python", "SQL", "R", "Java", "C#", "JavaScript", "TypeScript", "C++", "Scala", "Julia",
    ],
    "ML / DL Frameworks": [
        "PyTorch", "TensorFlow", "Keras", "scikit-learn", "XGBoost", "LightGBM", "JAX",
        "Hugging Face", "ONNX", "Deep Learning", "CNN", "RNN", "LSTM",
    ],
    "LLM / GenAI": [
        "LLM", "Prompt Engineering", "RAG", "LangChain", "LlamaIndex", "Fine-tuning",
        "Vector Database", "OpenAI API", "Anthropic API", "Transformers", "Generative AI",
        "Embeddings", "Semantic Search",
    ],
    "Agentic AI": [
        "Agents", "Multi-Agent Systems", "Tool Use / Function Calling",
        "AutoGen", "CrewAI", "LangGraph", "MCP", "Semantic Kernel", "Claude Agent SDK",
        "A2A Protocol", "Agent Skills", "Copilot",
    ],
    "Enterprise LLM Platforms": ["Gemini", "AWS Bedrock", "Vertex AI", "Azure OpenAI"],
    "AI Evaluation & Safety": [
        "LLM Evaluation", "LLM-as-a-Judge", "Red-teaming", "Hallucination Detection", "Observability",
        "Responsible AI", "Human-in-the-Loop", "PII", "HIPAA", "Differential Privacy",
        "LangSmith", "Ragas", "DeepEval", "Arize",
    ],
    "Computer Vision / NLP": [
        "Computer Vision", "NLP", "OpenCV", "Scikit-image", "Speech Recognition",
        "NLTK", "SpaCy", "Gensim", "LSA", "BERT",
    ],
    "Data Science / Analysis": [
        "Pandas", "NumPy", "SciPy", "Matplotlib", "Seaborn", "Statistics", "Hypothesis Testing",
        "Causal Inference", "Uncertainty Quantification", "A/B Testing", "Data Visualization", "Jupyter",
    ],
    "Feature Engineering": ["PCA", "NMF"],
    "Classical ML Models": [
        "ARIMA", "SARIMA", "SVM", "LDA", "Random Forest", "Gradient Boosting", "K-means",
        "KNN", "DBSCAN", "Hierarchical Clustering", "Clustering", "Time Series Forecasting",
        "Reinforcement Learning", "SHAP", "Recommendation Systems", "Collaborative Filtering",
    ],
    "Data Engineering / Big Data": [
        "Spark", "Hadoop", "Kafka", "Airflow", "ETL", "dbt", "Snowflake", "Databricks",
        "BigQuery", "Data Pipelines", "Data Modeling",
    ],
    "Databases": ["PostgreSQL", "MySQL", "MongoDB", "Redis", "Elasticsearch", "DynamoDB", "NoSQL"],
    "Cloud / Infra": [
        "AWS", "GCP", "Azure", "Docker", "Kubernetes", "Terraform", "CI/CD", "Linux",
        "Flask", "Streamlit", "Microservices", "REST API", "gRPC", "Event-Driven Architecture",
    ],
    "Software Engineering": ["Distributed Systems", "API Design & Development", "Software Testing"],
    "AI Coding Tools": ["Claude Code", "Cursor", "Codex"],
    "Web Scraping": ["Beautiful Soup", "Selenium"],
    "MLOps": ["MLOps", "MLflow", "Model Monitoring", "Feature Store"],
    "Version Control / Collaboration": ["Git", "Jira", "Confluence"],
    "Product Management": [
        "Roadmapping", "User Research", "Agile", "Scrum", "Product Analytics", "SQL Analytics",
        "Stakeholder Management", "Go-to-Market", "PRD Writing", "Wireframing",
        "Customer Discovery", "OKRs",
    ],
    "Education": ["PhD", "Master's Degree"],
}

# Fail fast at import time if the two structures ever drift apart — e.g. a new
# skill added to SKILL_TAXONOMY but forgotten here, or a typo in a category
# list that doesn't match the taxonomy's exact key spelling.
_categorized = {name for names in SKILL_CATEGORIES.values() for name in names}
_taxonomy_keys = set(SKILL_TAXONOMY.keys())
if _categorized != _taxonomy_keys:
    missing_categories = _taxonomy_keys - _categorized  # in taxonomy, no category
    unknown_skills = _categorized - _taxonomy_keys        # in a category, not in taxonomy (likely a typo)
    raise ValueError(
        f"SKILL_TAXONOMY / SKILL_CATEGORIES are out of sync. "
        f"Missing category for: {sorted(missing_categories)}. "
        f"Unknown skill names in SKILL_CATEGORIES: {sorted(unknown_skills)}."
    )

# Sibling skills specific/interchangeable enough that having ANY one of them
# counts as covering a JD's ask for a different one — e.g. a JD wanting GCP
# is substantially satisfied by AWS experience, the way a real ATS/skills
# taxonomy (Lightcast, O*NET) groups specific tools under a shared parent
# competency instead of treating them as unrelated strings. Deliberately NOT
# exhaustive — e.g. Docker/Kubernetes are excluded on purpose: knowing Docker
# doesn't mean you know K8s orchestration at scale, so grouping them would
# overstate the substitution. See analysis/skill_match.py for how this is
# used (binary credit — group membership either counts or it doesn't, no
# fractional weight, since no fractional number here is more justified than
# any other without real calibration data).
SKILL_GROUPS: dict[str, list[str]] = {
    "Cloud Platform": ["AWS", "GCP", "Azure"],
    "Deep Learning Framework": ["PyTorch", "TensorFlow", "Keras", "JAX"],
    "Tree Ensemble Method": ["Random Forest", "Gradient Boosting", "XGBoost", "LightGBM"],
    "Enterprise LLM Platform": ["Gemini", "AWS Bedrock", "Vertex AI", "Azure OpenAI"],
    "LLM Provider API": ["OpenAI API", "Anthropic API"],
    "LLM Eval/Observability Tool": ["LangSmith", "Ragas", "DeepEval", "Arize"],
    "LLM Orchestration Framework": [
        "LangChain", "LangGraph", "LlamaIndex", "Semantic Kernel", "AutoGen", "CrewAI", "Claude Agent SDK",
    ],
    "Data Warehouse/Lakehouse": ["Snowflake", "Databricks", "BigQuery"],
    "Relational Database": ["PostgreSQL", "MySQL"],
    "NLP Library": ["NLTK", "SpaCy", "Gensim"],
    "Time Series Model": ["ARIMA", "SARIMA", "Time Series Forecasting"],
    "Data Visualization Library": ["Data Visualization", "Matplotlib", "Seaborn"],
    "Clustering Method": ["K-means", "Hierarchical Clustering", "DBSCAN", "Clustering"],
    # Interchangeable agentic coding tools — fluency in one transfers to another
    # within days, unlike e.g. Docker vs Kubernetes (added 2026-09-15).
    "AI Coding Assistant": ["Claude Code", "Cursor", "Codex"],
}

_grouped_skills = {name for names in SKILL_GROUPS.values() for name in names}
_unknown_group_members = _grouped_skills - _taxonomy_keys
if _unknown_group_members:
    raise ValueError(f"SKILL_GROUPS references skills not in SKILL_TAXONOMY: {sorted(_unknown_group_members)}")

_SKILL_TO_GROUP: dict[str, str] = {
    name: group for group, names in SKILL_GROUPS.items() for name in names
}


def skill_group_of(skill_name: str) -> str | None:
    """Which SKILL_GROUPS group a given canonical skill name belongs to, or
    None if it's not part of any group."""
    return _SKILL_TO_GROUP.get(skill_name)


def skills_in_category(category: str) -> list[str]:
    """List of canonical skill names belonging to a theme, for building a
    `WHERE skill_name IN (...)` clause for theme-level stats."""
    return SKILL_CATEGORIES.get(category, [])


def category_of(skill_name: str) -> str | None:
    """Reverse lookup: which category a given canonical skill name belongs to."""
    for category, names in SKILL_CATEGORIES.items():
        if skill_name in names:
            return category
    return None


def _spaces_match_any_whitespace(pattern: str) -> str:
    """A literal space in a variant matches any whitespace run, so a skill
    wrapped across lines ("Prompt\\nEngineering", common in resume PDFs) still
    matches. Where a variable-width \\s+ would be invalid or change meaning —
    inside a [class], inside a lookbehind (must be fixed-width), or before a
    quantifier (" ?" -> "\\s+?" would turn optional into lazy) — the space
    becomes a single \\s instead."""
    out = []
    in_class = False
    groups: list[bool] = []  # True for each open lookbehind group
    i = 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\":
            out.append(pattern[i:i + 2])
            i += 2
            continue
        if in_class:
            if ch == "]":
                in_class = False
            out.append(r"\s" if ch == " " else ch)
        elif ch == "[":
            in_class = True
            out.append(ch)
        elif ch == "(":
            groups.append(pattern.startswith(("(?<=", "(?<!"), i))
            out.append(ch)
        elif ch == ")":
            if groups:
                groups.pop()
            out.append(ch)
        elif ch == " ":
            next_ch = pattern[i + 1:i + 2]
            fixed_width = any(groups) or next_ch in ("?", "*", "+", "{")
            out.append(r"\s" if fixed_width else r"\s+")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


# Pre-compile one regex per skill, combining all its variants with OR (|).
# re.IGNORECASE means "Python" matches "python"/"PYTHON"/"Python" alike.
_COMPILED_PATTERNS: dict[str, re.Pattern] = {
    canonical: re.compile(
        r"(?:" + "|".join(_spaces_match_any_whitespace(v) for v in variants) + r")",
        re.IGNORECASE,
    )
    for canonical, variants in SKILL_TAXONOMY.items()
}


def extract_skills(text: str) -> list[str]:
    """Return the canonical skill names found anywhere in `text`.

    No section parsing, no structure awareness — just presence/absence of
    each known term anywhere in the blob. Order of results follows the
    taxonomy's definition order, not order of appearance in the text.
    """
    if not text:
        return []

    found = []
    for canonical, pattern in _COMPILED_PATTERNS.items():
        if pattern.search(text):
            found.append(canonical)
    return found


if __name__ == "__main__":
    # Quick manual smoke test — paste real JD text here later to sanity-check.
    sample = """
    We're looking for a Machine Learning Engineer with strong Python, NumPy,
    Pandas, and Scikit-Learn skills. Experience with PyTorch or TensorFlow
    (CNN, RNN, LSTM) required. Familiarity with AWS, GCP, Docker, and
    Kubernetes a plus, along with Linux and Flask for deployment. You'll
    build RAG pipelines using LangChain, fine-tune LLMs, and use BeautifulSoup
    and Selenium for data collection. Bonus: NLTK, SpaCy, BERT, PCA, Random
    Forest, XGBoost, K-means clustering, ARIMA time series forecasting, and
    SHAP for model evaluation. SQL and experimentation (A/B testing)
    experience preferred. PhD not required but a plus.
    """
    print(extract_skills(sample))
