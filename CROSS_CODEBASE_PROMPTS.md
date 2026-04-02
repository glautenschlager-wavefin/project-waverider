# Cross-Codebase Test Prompts for Waverider

These prompts are designed to exercise Waverider's `search_codebase` and `retrieve_code`
MCP tools across all 6 indexed codebases, testing the agent's ability to synthesize
answers from multiple services.

## Index Summary

| Codebase | Language | Files | Embeddings | Neo4j Functions | Neo4j Classes |
|---|---|---|---|---|---|
| **waverider** | Python | 16 | 187 | ~50 | ~10 |
| **embedded-payroll** | Python | 350 | 7,832 | 7,150 | 1,652 |
| **identity** | Python | 868 | 12,706 | 5,949 | 1,701 |
| **reef** | TypeScript | 1,450 | 13,875 | 2,961 | 1,403 |
| **payroll** | Ruby | 3,584 | 11,267 | 8,188 | 2,654 |
| **next-wave** | TSX/TS/JSX | 9,141 | 70,461 | 12,317 | 470 |
| **Total** | — | **15,409** | **116,328** | **36,615** | **7,890** |

---

## Category 1: Single-Codebase Deep Dives

These prompts test RAG quality on individual codebases.

### 1.1 Identity — Authentication Flow
```
Using the identity codebase, explain how user login authentication works
end-to-end. What are the key functions involved and what security checks are performed?
```

### 1.2 Payroll — Salary Calculation
```
In the payroll codebase, how are employee wages calculated? Trace the flow
from input to final pay stub generation, including tax calculations.
```

### 1.3 Reef — GraphQL Architecture
```
In the reef codebase, how are GraphQL resolvers structured? Show me the pattern
for how a module (e.g., payments or bills) exposes its data through GraphQL.
```

### 1.4 Next-Wave — Invoice UI Components
```
In the next-wave codebase, how is the invoice creation flow implemented?
Walk me through the React components, state management, and API calls involved.
```

### 1.5 Embedded-Payroll — API Design
```
In the embedded-payroll codebase, what API endpoints are exposed and how
are they structured? What authentication/authorization patterns are used?
```

---

## Category 2: Cross-Codebase Boundary Prompts

These prompts require searching MULTIPLE codebases to answer fully.

### 2.1 Payroll Pipeline: Frontend → BFF → Backend
```
How does payroll processing work across the Wave stack?
Search next-wave for the payroll UI components, reef for the BFF/API layer,
and payroll for the backend calculation engine. How do they connect?
```
- **Expected tools**: `retrieve_code(codebase_name="next-wave")`, `retrieve_code(codebase_name="reef")`, `retrieve_code(codebase_name="payroll")`
- **Tests**: Multi-codebase synthesis, understanding API boundaries

### 2.2 Authentication End-to-End
```
Trace the authentication flow from the login page to the backend.
Check next-wave for the login UI, reef for auth middleware, and identity
for the actual authentication logic. How does the auth token flow between services?
```
- **Expected tools**: `search_codebase(query="login", codebase_name="next-wave")`, `search_codebase(query="authentication", codebase_name="reef")`, `retrieve_code(codebase_name="identity")`
- **Tests**: Cross-service auth flow understanding

### 2.3 Invoice/Billing Data Flow
```
How does invoice data flow through Wave? Find the invoice models in identity,
the invoice processing in reef's BFF layer, and the UI components in next-wave.
What data transformations happen at each layer?
```
- **Expected tools**: Multiple `retrieve_code` calls across 3 codebases
- **Tests**: Data model transformation tracking across service boundaries

### 2.4 Employee Onboarding Across Services
```
When a new employee is added, what happens across all Wave services?
Check embedded-payroll and payroll for the backend employee creation logic,
identity for user account provisioning, and next-wave for the onboarding UI.
```
- **Expected tools**: 4 codebase searches
- **Tests**: Cross-service workflow understanding

### 2.5 Payments Architecture
```
How are payments handled across the Wave platform? Search reef for the
payments module (clients, transformers, types), payroll for payment-related
Ruby models, and next-wave for the payment UI. What external services are called?
```
- **Expected tools**: `search_codebase(query="payment", codebase_name="reef")`, `retrieve_code(codebase_name="payroll")`, `retrieve_code(codebase_name="next-wave")`
- **Tests**: External service integration patterns

---

## Category 3: Architectural Pattern Comparison

### 3.1 API Client Patterns Across Languages
```
Compare the API client patterns used in each codebase:
- Python HTTP clients in identity and embedded-payroll
- TypeScript clients in reef
- React API hooks in next-wave
What patterns are consistent and what differs?
```

### 3.2 Testing Patterns Across Services
```
How do testing patterns differ across Wave services? Compare:
- Python pytest patterns in identity
- Ruby RSpec patterns in payroll
- TypeScript Jest patterns in reef and next-wave
Show representative test examples from each.
```

### 3.3 Error Handling Comparison
```
How does error handling differ across the Wave stack? Find error handling
patterns in identity (Python), payroll (Ruby), reef (TypeScript), and
next-wave (React). Are there shared error codes or patterns?
```

### 3.4 Data Serialization Patterns
```
How is data serialized/deserialized across Wave services? Look at:
- Avro schemas in identity
- Transformers in reef
- Serializers in payroll (Ruby)
- GraphQL types in next-wave
What formats and patterns are used at service boundaries?
```

---

## Category 4: Targeted Discovery Prompts

### 4.1 Find All Feature Flag Implementations
```
Search all codebases for feature flag usage. How are feature flags
implemented in identity (Python), reef (TypeScript), and next-wave (React)?
Is there a shared feature flag service?
```

### 4.2 Database/ORM Patterns
```
What database access patterns are used across Wave?
- Django ORM in identity
- ActiveRecord in payroll
- What does reef use for data access?
Compare the query patterns and model definitions.
```

### 4.3 Background Job Processing
```
How do background jobs work across Wave services? Search payroll for
Sidekiq/background worker patterns, identity for Celery tasks, and
reef for any async processing. What job patterns are shared?
```

---

## How to Run These Prompts

1. Open VS Code with the Waverider workspace
2. Start a new Copilot chat
3. Paste any prompt above
4. The agent should use Waverider MCP tools (`search_codebase` and `retrieve_code`)
   to search across the indexed codebases
5. Evaluate:
   - Did the agent use the correct `codebase_name` for each search?
   - Did it search multiple codebases when the prompt required cross-boundary thinking?
   - Did it synthesize a coherent answer from code across different languages/services?
   - Were the returned snippets relevant?
   - Was the response time acceptable? (Embedding lookup should be <1s per codebase)
