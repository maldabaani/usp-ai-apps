#!/bin/bash
set -e

SAAS_CLINIC_DIR="${1:-$HOME/Downloads/claude-project/saas-clinic}"
SESSION_REPO="https://github.com/maldabaani/claude.git"
SESSION_BRANCH="claude/clinic-saas-architecture-plan-F0XSq"

echo "==> Syncing clinic-saas work to: $SAAS_CLINIC_DIR"

if [ ! -d "$SAAS_CLINIC_DIR/.git" ]; then
  echo "ERROR: $SAAS_CLINIC_DIR is not a git repo. Pass the correct path as argument."
  echo "Usage: bash sync-to-saas-clinic.sh /path/to/saas-clinic"
  exit 1
fi

# Clone session repo into a temp dir
TMPDIR=$(mktemp -d)
echo "==> Cloning session repo to $TMPDIR ..."
git clone --depth 1 --branch "$SESSION_BRANCH" "$SESSION_REPO" "$TMPDIR/session"

# Overwrite clinic-saas folder in target repo
echo "==> Copying clinic-saas/ into $SAAS_CLINIC_DIR ..."
rm -rf "$SAAS_CLINIC_DIR/clinic-saas"
cp -r "$TMPDIR/session/clinic-saas" "$SAAS_CLINIC_DIR/"

# Commit and push
cd "$SAAS_CLINIC_DIR"
git checkout main 2>/dev/null || git checkout -b main
git add clinic-saas/
git commit -m "feat: full clinic-saas build from Claude session

Complete Medical Clinic SaaS — Spring Boot 3 backend + Angular 17 frontend:
- Multi-tenant architecture (separate DB per clinic, Flyway migrations)
- JWT auth, static RBAC (ADMIN/DOCTOR/NURSE/RECEPTIONIST)
- Patient management, visits, vitals, diagnoses
- Lab orders + results entry
- Radiology orders + report entry
- Prescriptions + medication catalog
- Invoices + payment recording
- Allergies + medical history
- Appointment scheduler with booking form
- Staff management UI
- Platform admin (manage clinics/tenants)"

echo "==> Pushing to origin main ..."
git push origin main

# Cleanup
rm -rf "$TMPDIR"
echo ""
echo "Done! All changes are now in maldabaani/saas-clinic main."
