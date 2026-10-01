# Java standards (Spring Boot 3, Maven)

Rule ids are cited by the Reviewer as `rule_ref`. Edit freely; keep the `**ID**` markers.

These rules assume a Spring Boot service. Before citing JAVA-001's package layout, JAVA-002,
JAVA-004, JAVA-010, JAVA-011, JAVA-012 or JAVA-030's `@WebMvcTest`/`MockMvc` requirement, check
whether the project actually has a Spring Boot web layer (controllers, `@RestController`,
HTTP endpoints) -- a plain Java library or domain-model project with no web layer has nothing
for those rules to apply to, and a domain exception thrown at a method boundary (not an HTTP
response) is correct there, not a JAVA-012 violation. Match the project's own existing
convention over a rule that assumes a layer this project doesn't have.

## Structure
- **JAVA-001** Java 21. Spring Boot projects: Spring Boot 3, base package from the template with sub-packages `controller`, `service`, `repository`, `model`, `dto`. (Package layout is Spring Boot only.)
- **JAVA-002** Layering: controllers call services, services call repositories. Controllers never touch repositories. (Spring Boot services only.)
- **JAVA-003** Constructor injection only (final fields); no `@Autowired` on fields or setters.
- **JAVA-004** Controllers accept and return DTOs (Java `record`s), never JPA entities. (Spring Boot services only.)
- **JAVA-005** Interfaces only where there is more than one implementation or a test seam is needed.

## API behavior
- **JAVA-010** `@RestController` + `@RequestMapping("/api/<resource>")`; `ResponseEntity` with correct status codes (201 create, 204 delete, 404 missing). (Spring Boot services only.)
- **JAVA-011** Validate request DTOs with Jakarta Bean Validation (`@Valid`, `@NotBlank`, ...). (Spring Boot services only.)
- **JAVA-012** Map exceptions centrally with `@RestControllerAdvice`; no stack traces in responses. (Spring Boot services only -- a plain exception thrown at a non-HTTP method boundary is correct, not a violation.)

## Quality
- **JAVA-020** No `System.out`; use SLF4J logging.
- **JAVA-021** Prefer immutability: records for DTOs, `List.copyOf` for exposed collections.
- **JAVA-022** Configuration in `application.yml`; no hardcoded secrets. (Spring Boot projects; a plain library has no `application.yml` to put it in.)

## Tests
- **JAVA-030** JUnit 5 + AssertJ. Unit-test services with Mockito. Spring Boot projects: test controllers with `@WebMvcTest` + `MockMvc`.
- **JAVA-031** Tests live under `src/test/java` mirroring the main package; names end in `Test`.
