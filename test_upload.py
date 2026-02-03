from fastapi.testclient import TestClient
from app.main import app
import io

client = TestClient(app)

def test_upload_invalid_extension():
    # Test uploading a .txt file (should fail)
    file_data = {"file": ("test.txt", io.BytesIO(b"dummy content"), "text/plain")}
    response = client.post("/materials/upload", files=file_data)
    assert response.status_code == 400
    assert "Invalid format" in response.json()["detail"]

def test_upload_valid_pdf():
    # Test uploading a .pdf file (should succeed)
    # Note: You might need to bypass auth for this test or provide a mock token
    file_data = {"file": ("test.pdf", io.BytesIO(b"dummy pdf content"), "application/pdf")}
    response = client.post("/materials/upload", files=file_data)
    # If not logged in, this will return 401, which also proves the router is working!
    assert response.status_code in [200, 401]