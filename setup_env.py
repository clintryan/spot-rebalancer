#!/usr/bin/env python3
"""
Setup script to configure environment variables for the trading bot
"""
import os

def setup_environment():
    """Interactive setup for environment variables"""
    print("🔧 Setting up environment variables for EMA Trading Bot")
    print("=" * 50)
    
    # Get clintbot credentials
    print("\n📝 Enter your clintbot subaccount API credentials:")
    api_key = input("BYBIT_API_KEY_clintbot: ").strip()
    api_secret = input("BYBIT_API_SECRET_clintbot: ").strip()
    
    if not api_key or not api_secret:
        print("❌ Error: Both API key and secret are required")
        return False
    
    # Create .env file content
    env_content = f"""# Bybit API Credentials for clintbot subaccount
BYBIT_API_KEY_clintbot={api_key}
BYBIT_API_SECRET_clintbot={api_secret}

# Bybit API Credentials for bybitwood main account (keeping for reference)
BYBIT_API_KEY_bybitwood=your_main_account_api_key_here
BYBIT_API_SECRET_bybitwood=your_main_account_api_secret_here
"""
    
    # Write .env file
    try:
        with open('.env', 'w') as f:
            f.write(env_content)
        print("\n✅ .env file created successfully!")
        print("🔒 Make sure to add .env to your .gitignore to keep credentials secure")
        return True
    except Exception as e:
        print(f"❌ Error creating .env file: {e}")
        return False

def test_environment():
    """Test that environment variables are loaded correctly"""
    print("\n🧪 Testing environment variable loading...")
    
    try:
        from dotenv import load_dotenv
        load_dotenv()
        
        api_key = os.getenv('BYBIT_API_KEY_clintbot')
        api_secret = os.getenv('BYBIT_API_SECRET_clintbot')
        
        if api_key and api_secret:
            print(f"✅ API Key loaded: {api_key[:8]}...")
            print(f"✅ API Secret loaded: {api_secret[:8]}...")
            return True
        else:
            print("❌ Environment variables not found")
            return False
    except Exception as e:
        print(f"❌ Error testing environment: {e}")
        return False

if __name__ == "__main__":
    print("EMA Trading Bot - Environment Setup")
    print("=" * 40)
    
    if os.path.exists('.env'):
        print("📄 .env file already exists")
        overwrite = input("Do you want to overwrite it? (y/N): ").strip().lower()
        if overwrite != 'y':
            print("Keeping existing .env file")
            test_environment()
            exit(0)
    
    if setup_environment():
        test_environment()
        print("\n🎉 Setup complete! You can now run the bot with:")
        print("   python simplified-main.py")
    else:
        print("\n❌ Setup failed. Please try again.")
