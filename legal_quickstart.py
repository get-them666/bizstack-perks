#!/usr/bin/env python3
"""
Legal Document Business Writer - Quick Start Script
Run this script to initialize and test the legal document system
"""

import os
import sys
from pathlib import Path
import json
from datetime import datetime

def print_header(text):
    """Print formatted header."""
    print("\n" + "="*60)
    print(f"  {text}")
    print("="*60)

def print_step(number, text):
    """Print numbered step."""
    print(f"\n[{number}] {text}")

def check_dependencies():
    """Check if all required dependencies are installed."""
    print_header("CHECKING DEPENDENCIES")
    
    dependencies = {
        'fastapi': 'FastAPI',
        'sqlalchemy': 'SQLAlchemy',
        'PyPDF2': 'PyPDF2',
        'docx': 'python-docx',
        'dotenv': 'python-dotenv',
        'werkzeug': 'Werkzeug',
    }
    
    missing = []
    for module, name in dependencies.items():
        try:
            __import__(module)
            print(f"  ✓ {name}")
        except ImportError:
            print(f"  ✗ {name} - MISSING")
            missing.append(name)
    
    if missing:
        print(f"\n⚠️  Missing dependencies: {', '.join(missing)}")
        print(f"Install with: pip install -r legal_requirements.txt")
        return False
    
    print("\n✓ All dependencies installed!")
    return True

def create_directories():
    """Create required directories."""
    print_header("CREATING DIRECTORIES")
    
    directories = [
        "./legal_templates",
        "./generated_documents",
        "./uploaded_documents",
    ]
    
    for directory in directories:
        Path(directory).mkdir(exist_ok=True)
        print(f"  ✓ {directory}")
    
    print("\n✓ All directories created!")

def check_configuration():
    """Check configuration."""
    print_header("CHECKING CONFIGURATION")
    
    from dotenv import load_dotenv
    load_dotenv()
    
    config_items = {
        'DATABASE_URL': 'Database',
        'LEGAL_EMAIL_ADDRESS': 'Email Address',
        'TWILIO_ACCOUNT_SID': 'Twilio Account (optional)',
    }
    
    configured = 0
    for env_var, label in config_items.items():
        value = os.getenv(env_var)
        if value:
            print(f"  ✓ {label} configured")
            configured += 1
        else:
            print(f"  ⚠️  {label} NOT configured")
    
    print(f"\n{configured}/{len(config_items)} items configured")
    
    if configured < len(config_items):
        print("\nTo configure:")
        print("  1. Copy .env.example to .env")
        print("  2. Fill in your configuration values")
        print("  3. Re-run this script")

def initialize_database():
    """Initialize the database."""
    print_header("INITIALIZING DATABASE")
    
    try:
        from legal_models import Base
        from sqlalchemy import create_engine
        from dotenv import load_dotenv
        
        load_dotenv()
        
        database_url = os.getenv('DATABASE_URL', 'sqlite:///legal_documents.db')
        print(f"  Using database: {database_url}")
        
        engine = create_engine(database_url)
        Base.metadata.create_all(engine)
        
        print("  ✓ Database tables created")
        return True
    except Exception as e:
        print(f"  ✗ Error initializing database: {e}")
        return False

def test_legal_writer():
    """Test the legal document writer."""
    print_header("TESTING LEGAL DOCUMENT WRITER")
    
    try:
        from legal_document_writer import LegalDocumentWriter
        
        writer = LegalDocumentWriter({
            "library_path": "./legal_templates",
            "documents_dir": "./generated_documents"
        })
        
        # List templates
        templates = writer.library.list_templates()
        print(f"  ✓ Loaded {len(templates)} templates")
        
        # Display template categories
        categories = writer.library.list_categories()
        print(f"  ✓ Available categories: {', '.join(categories)}")
        
        # Generate a test document
        print("\n  Generating test NDA...")
        result = writer.generate_document(
            "nda",
            {
                "party_name": "Test Company",
                "disclosure_period": "2 years",
                "restriction_period": "3 years",
                "jurisdiction": "New York"
            },
            format="docx",
            filename="test_nda"
        )
        
        if result['status'] == 'success':
            print(f"  ✓ Test document generated: {Path(result['file']).name}")
            print(f"    Size: {result['size']} bytes")
            return True
        else:
            print(f"  ✗ Failed to generate test document: {result.get('message', 'Unknown error')}")
            return False
    
    except Exception as e:
        print(f"  ✗ Error testing legal writer: {e}")
        return False

def test_api_endpoints():
    """Test the API endpoints."""
    print_header("TESTING API ENDPOINTS")
    
    try:
        import requests
        
        # Note: This assumes the FastAPI app is running.
        base_url = "http://localhost:8000/api/legal"
        
        print(f"  Testing {base_url}")
        
        # Health check
        try:
            response = requests.get(f"{base_url}/health", timeout=2)
            if response.status_code == 200:
                print("  ✓ Health check passed")
                health = response.json()
                print(f"    PDF Support: {health.get('pdf_support')}")
                print(f"    Form Support: {health.get('form_support')}")
            else:
                print(f"  ✗ Health check failed: {response.status_code}")
        except requests.exceptions.ConnectionError:
            print("  ⚠️  API server not running (http://localhost:8000)")
            print("     Start the FastAPI app to enable API testing")
        
    except ImportError:
        print("  ⚠️  requests library not installed")
    except Exception as e:
        print(f"  ⚠️  Error testing API: {e}")

def generate_config_template():
    """Generate a .env.example file."""
    print_header("GENERATING CONFIGURATION TEMPLATE")
    
    env_template = """# Legal Document Business Writer Configuration

# Email Configuration
LEGAL_SMTP_SERVER=smtp.gmail.com
LEGAL_SMTP_PORT=587
LEGAL_EMAIL_ADDRESS=your-email@gmail.com
LEGAL_EMAIL_PASSWORD=your-app-password
LEGAL_SMTP_TLS=true

# SMS Configuration (Twilio)
TWILIO_ACCOUNT_SID=ACxxxxxxxxxxxxxxxxxxxxx
TWILIO_AUTH_TOKEN=your-auth-token
TWILIO_PHONE_NUMBER=+1234567890

# Database
DATABASE_URL=sqlite:///legal_documents.db
# For PostgreSQL: DATABASE_URL=postgresql://user:password@localhost/bizstack_perks

# Feature Flags
ENABLE_PDF_SUPPORT=true
ENABLE_DOCX_SUPPORT=true
ENABLE_FORM_SUPPORT=true
ENABLE_EMAIL=true
ENABLE_SMS=false
ENABLE_ESIGNATURE=false

# Security
REQUIRE_AUTH=true
ENABLE_ENCRYPTION=false
ENCRYPTION_KEY=your-encryption-key-here

# Logging
LOG_LEVEL=INFO

# Storage
STORAGE_TYPE=local
"""
    
    env_path = Path(".env.example")
    if not env_path.exists():
        with open(env_path, 'w') as f:
            f.write(env_template)
        print(f"  ✓ Created {env_path}")
    else:
        print(f"  • {env_path} already exists")

def print_summary(results):
    """Print summary of initialization."""
    print_header("INITIALIZATION SUMMARY")
    
    print("\n✅ Legal Document Business Writer is ready!")
    
    print("\nNext steps:")
    print("  1. Update .env file with your configuration")
    print("  2. Start the FastAPI application: uvicorn main:app --reload --port 8000")
    print("  3. Access the web interface at: http://localhost:8000/legal")
    print("  4. API available at: http://localhost:8000/api/legal")
    
    print("\nUseful commands:")
    print("  - Generate document: python legal_document_writer.py")
    print("  - List templates: python -c 'from legal_document_writer import LegalDocumentWriter; print([t[\"name\"] for t in LegalDocumentWriter().library.list_templates()])'")
    
    print("\nDocumentation:")
    print("  - Setup Guide: LEGAL_SETUP_GUIDE.py")
    print("  - README: LEGAL_DOCUMENTS_README.md")
    print("  - API Docs: http://localhost:8000/docs")

def main():
    """Run initialization."""
    print("\n")
    print("╔════════════════════════════════════════════════════════════╗")
    print("║                                                            ║")
    print("║     Legal Document Business Writer - Quick Start           ║")
    print("║     BizStack Perks Application                            ║")
    print("║                                                            ║")
    print("╚════════════════════════════════════════════════════════════╝")
    
    results = {
        'dependencies': False,
        'directories': False,
        'database': False,
        'writer': False,
    }
    
    # Run checks
    print_step(1, "Checking dependencies...")
    results['dependencies'] = check_dependencies()
    
    if not results['dependencies']:
        print("\n⚠️  Please install missing dependencies first.")
        sys.exit(1)
    
    print_step(2, "Creating directories...")
    create_directories()
    results['directories'] = True
    
    print_step(3, "Generating configuration template...")
    generate_config_template()
    
    print_step(4, "Checking configuration...")
    check_configuration()
    
    print_step(5, "Initializing database...")
    results['database'] = initialize_database()
    
    print_step(6, "Testing legal document writer...")
    results['writer'] = test_legal_writer()
    
    print_step(7, "Testing API endpoints...")
    test_api_endpoints()
    
    # Summary
    print_summary(results)
    
    print("\n" + "="*60)
    print("  Initialization Complete!")
    print("="*60 + "\n")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nInitialization cancelled.")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error during initialization: {e}")
        sys.exit(1)
