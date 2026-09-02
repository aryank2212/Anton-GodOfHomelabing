import re

KNOWN_TECHNOLOGIES = [
    "CUDA", "Python", "PyTorch", "TensorFlow", "React", "Vue", "Angular",
    "Docker", "Kubernetes", "SQL", "NoSQL", "Redis", "MongoDB", "PostgreSQL",
    "Linux", "Windows", "macOS", "Git", "AWS", "GCP", "Azure", "FastAPI",
    "Flask", "Django", "Node.js", "TypeScript", "JavaScript", "Rust", "Go",
    "C++", "Java", "Kotlin", "Swift", "Ruby", "Scala", "Haskell", "Clojure",
    "Elixir", "Phoenix", "GraphQL", "REST", "gRPC", "WebSocket", "OAuth",
    "JWT", "SQLAlchemy", "Pydantic", "Celery", "RabbitMQ", "Kafka",
    "Spark", "Hadoop", "Flink", "Airflow", "Terraform", "Ansible",
    "Prometheus", "Grafana", "ELK", "Selenium", "Cypress", "Jest",
    "OpenAI", "Ollama", "LangChain", "Chroma", "Pinecone", "Weaviate",
    "Qdrant", "Milvus", "Hugging Face", "Transformers", "Diffusers",
    "Stable Diffusion", "Midjourney", "ChatGPT", "Claude", "Copilot",
]

SKIP_WORDS = {
    "The", "This", "That", "What", "When", "Where", "How", "Why",
    "My", "Our", "Your", "His", "Her", "Its", "Their",
    "It", "He", "She", "They", "We", "I", "You",
    "A", "An", "And", "But", "Or", "For", "Nor", "Yet", "So",
    "Not", "All", "Any", "Each", "Every", "No", "Some", "Such",
    "Only", "Just", "Then", "Now", "Today", "Yesterday", "Tomorrow",
    "Here", "There", "One", "Two", "Three", "First", "Last", "Next",
    "Previous", "Final", "Initial", "Main", "Major", "Minor",
    "Great", "Good", "Bad", "New", "Old", "Big", "Small", "Long",
    "Short", "High", "Low", "Top", "Bottom", "Left", "Right",
    "Up", "Down", "In", "Out", "On", "Off", "Over", "Under",
    "Above", "Below", "Before", "After", "During", "Without",
    "Within", "Between", "Among", "Through", "Beyond", "Across",
    "Around", "About", "Against", "Along", "Inside", "Outside",
    "Toward", "Away", "Back", "Forward", "Still", "Already",
    "Always", "Never", "Often", "Sometimes", "Rarely", "Usually",
    "Eventually", "Finally", "Suddenly", "However", "Therefore",
    "Moreover", "Furthermore", "Nevertheless", "Meanwhile",
    "Also", "Well", "Indeed", "Perhaps", "Maybe", "Hello",
    "Hi", "Dear", "Thanks", "Thank", "Please", "Sorry",
    "Yes", "No", "Okay", "Fine", "Sure", "Right", "Wrong",
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
}


def extract_entities(text: str) -> list[dict]:
    entities = []
    seen = set()

    for tech in KNOWN_TECHNOLOGIES:
        pattern = re.compile(re.escape(tech), re.IGNORECASE)
        if pattern.search(text) and tech not in seen:
            entities.append({"name": tech, "entity_type": "technology"})
            seen.add(tech)

    quoted = re.findall(r'"([^"]{4,})"', text)
    for q in quoted:
        if q not in seen:
            entities.append({"name": q, "entity_type": "book"})
            seen.add(q)

    multi_caps = re.findall(r"([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)", text)
    for phrase in multi_caps:
        if phrase not in seen and phrase not in KNOWN_TECHNOLOGIES:
            entities.append({"name": phrase, "entity_type": "person"})
            seen.add(phrase)

    single_caps = re.findall(r"(?:[.!?]\s+|\b)([A-Z][a-z]{2,})(?:\b)", text)
    for word in single_caps:
        if word not in seen and word not in SKIP_WORDS:
            entities.append({"name": word, "entity_type": "unknown"})
            seen.add(word)

    return entities
