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
    "SQL": ["sql"],
    "R": ["r programming", r"\br\b"],
    "Java": ["java(?!script)"],
    "C#": [r"\bc#", "c-sharp"],
    "JavaScript": ["javascript", r"\bjs\b"],
    "TypeScript": ["typescript"],
    "C++": [r"c\+\+"],
    "Go": ["golang", r"\bgo\b"],
    "Scala": ["scala"],
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
    "Fine-tuning": ["fine-tuning", "finetuning"],
    "Vector Database": ["vector database", "vector db", "vector store"],
    "OpenAI API": ["openai api", "gpt-4", "gpt4"],
    "Anthropic API": ["anthropic api", "claude api"],
    "Agents": ["ai agent", "agentic"],
    "Transformers": ["transformer model", "transformers library"],
    "Generative AI": ["generative ai", r"\bgenai\b", "gen ai"],
    "Multi-Agent Systems": ["multi-agent", "multiagent", "multi agent"],
    "Agent Orchestration": ["agent orchestration", "orchestration framework"],
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
    "Embeddings": [r"\bembeddings?\b"],
    "Semantic Search": ["semantic search"],

    # --- Enterprise LLM platforms ---
    "Gemini": [r"\bgemini\b"],
    "AWS Bedrock": ["aws bedrock", "amazon bedrock", r"\bbedrock\b"],
    "Vertex AI": ["vertex ai"],
    "Azure OpenAI": ["azure openai"],

    # --- AI evaluation & safety ---
    "AI Evals": ["ai evals", r"\bevals?\b", "evaluation harness"],
    "LLM-as-a-Judge": ["llm-as-a-judge", "llm as a judge"],
    "Red-teaming": ["red-teaming", "red teaming"],
    "Hallucination Detection": ["hallucination"],
    "Observability": ["observability"],
    "Responsible AI": ["responsible ai"],
    "Human-in-the-Loop": ["human-in-the-loop", "human in the loop"],
    "PII": [r"\bpii\b", "personally identifiable information"],
    "Differential Privacy": ["differential privacy"],

    # --- Computer vision / NLP ---
    "Computer Vision": ["computer vision", r"\bcv\b(?!\.)"],
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
    "A/B Testing": ["a/b test", "ab test", "experimentation"],
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
        # bare "recommendation(s)" and "recommender(s)" catch phrasing like
        # "personalized recommendations" or "content recommender" — not just
        # "recommendation system". The (?<!of ) exclusion guards against the
        # one common false-positive collision: "letter(s) of recommendation"
        # in academic/research postings, which has nothing to do with ML.
        r"(?<!of )\brecommendations?\b",
        r"\brecommenders?\b",
        r"\brecommending\b",
    ],
    "Collaborative Filtering": ["collaborative filtering"],

    # --- Data engineering / big data ---
    "Spark": ["apache spark", r"\bspark\b"],
    "Hadoop": ["hadoop"],
    "Kafka": ["kafka"],
    "Airflow": ["airflow"],
    "ETL": ["etl", "elt"],
    "dbt": [r"\bdbt\b"],
    "Snowflake": ["snowflake"],
    "Databricks": ["databricks"],
    "BigQuery": ["bigquery"],
    "PySpark": [r"\bpyspark\b"],

    # --- Databases ---
    "PostgreSQL": ["postgresql", "postgres"],
    "MySQL": ["mysql"],
    "MongoDB": ["mongodb", "mongo"],
    "Redis": ["redis"],
    "Elasticsearch": ["elasticsearch"],
    "DynamoDB": ["dynamodb"],

    # --- Cloud / infra ---
    "AWS": ["aws", "amazon web services"],
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

    # --- Web scraping ---
    "Beautiful Soup": ["beautiful soup", "beautifulsoup", r"\bbs4\b"],
    "Selenium": [r"\bselenium\b"],

    # --- MLOps ---
    "MLOps": ["mlops"],
    "MLflow": ["mlflow"],
    "Model Monitoring": ["model monitoring", "model drift"],
    "Feature Store": ["feature store"],

    # --- Version control / collaboration ---
    "Git": ["git", "github", "gitlab"],
    "Jira": ["jira"],
    "Confluence": ["confluence"],

    # --- Product management ---
    "Roadmapping": ["roadmap", "roadmapping"],
    "User Research": ["user research", "user interviews"],
    "Agile": ["agile"],
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
    "Master's Degree": ["master's degree", "ms degree", "msc"],
}

# Groups skill names into themes, e.g. "how many jobs mention ANYTHING in the
# Agentic AI theme" — needed because a theme-level stat can't just sum each
# skill's individual percentage (a job matching both "LangGraph" and "MCP"
# would get double-counted). Query with `WHERE skill_name IN (skills_in_category(...))`
# to correctly count each job once even if it matched multiple tags in the theme.
SKILL_CATEGORIES: dict[str, list[str]] = {
    "Programming Languages": [
        "Python", "SQL", "R", "Java", "C#", "JavaScript", "TypeScript", "C++", "Go", "Scala", "Julia",
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
        "Agents", "Multi-Agent Systems", "Agent Orchestration", "Tool Use / Function Calling",
        "AutoGen", "CrewAI", "LangGraph", "MCP", "Semantic Kernel", "Claude Agent SDK",
        "A2A Protocol", "Agent Skills", "Copilot",
    ],
    "Enterprise LLM Platforms": ["Gemini", "AWS Bedrock", "Vertex AI", "Azure OpenAI"],
    "AI Evaluation & Safety": [
        "AI Evals", "LLM-as-a-Judge", "Red-teaming", "Hallucination Detection", "Observability",
        "Responsible AI", "Human-in-the-Loop", "PII", "Differential Privacy",
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
        "BigQuery", "PySpark",
    ],
    "Databases": ["PostgreSQL", "MySQL", "MongoDB", "Redis", "Elasticsearch", "DynamoDB", "NoSQL"],
    "Cloud / Infra": [
        "AWS", "GCP", "Azure", "Docker", "Kubernetes", "Terraform", "CI/CD", "Linux",
        "Flask", "Streamlit", "Microservices", "REST API", "gRPC", "Event-Driven Architecture",
    ],
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


# Pre-compile one regex per skill, combining all its variants with OR (|).
# re.IGNORECASE means "Python" matches "python"/"PYTHON"/"Python" alike.
_COMPILED_PATTERNS: dict[str, re.Pattern] = {
    canonical: re.compile(
        r"(?:" + "|".join(variants) + r")",
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
