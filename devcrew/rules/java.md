# Java standards (Spring Boot 3, Maven)

Rule ids are cited by the Reviewer as `rule_ref`. Edit freely; keep the `**ID**` markers.

## Structure
- **JAVA-001** Java 21, Spring Boot 3. Base package from the template; sub-packages `controller`, `service`, `repository`, `model`, `dto`.
- **JAVA-002** Layering: controllers call services, services call repositories. Controllers never touch repositories.
- **JAVA-003** Constructor injection only (final fields); no `@Autowired` on fields or setters.
- **JAVA-004** Controllers accept and return DTOs (Java `record`s), never JPA entities.
- **JAVA-005** Interfaces only where there is more than one implementation or a test seam is needed.

## API behavior
- **JAVA-010** `@RestController` + `@RequestMapping("/api/<resource>")`; `ResponseEntity` with correct status codes (201 create, 204 delete, 404 missing).
- **JAVA-011** Validate request DTOs with Jakarta Bean Validation (`@Valid`, `@NotBlank`, ...).
- **JAVA-012** Map exceptions centrally with `@RestControllerAdvice`; no stack traces in responses.

## Quality
- **JAVA-020** No `System.out`; use SLF4J logging.
- **JAVA-021** Prefer immutability: records for DTOs, `List.copyOf` for exposed collections.
- **JAVA-022** Configuration in `application.yml`; no hardcoded secrets.

## Tests
- **JAVA-030** JUnit 5 + AssertJ. Unit-test services with Mockito; test controllers with `@WebMvcTest` + `MockMvc`.
- **JAVA-031** Tests live under `src/test/java` mirroring the main package; names end in `Test`.
