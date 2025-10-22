# Multi-Account Configuration

This bot now supports multiple Bybit accounts through a simple account name configuration.

## Setup

### 1. Configure Account in config.yaml

```yaml
api:
  account_name: 'Wood'  # Options: 'Wood', 'Hyma', or any other account name
  testnet: false
```

### 2. Set Environment Variables

Create a `.env` file with your API credentials using the format:

```bash
# Format: BYBIT_API_KEY_{ACCOUNT_NAME} and BYBIT_API_SECRET_{ACCOUNT_NAME}
BYBIT_API_KEY_Wood=your_wood_account_api_key_here
BYBIT_API_SECRET_Wood=your_wood_account_api_secret_here

BYBIT_API_KEY_Hyma=your_hyma_account_api_key_here
BYBIT_API_SECRET_Hyma=your_hyma_account_api_secret_here
```

### 3. Switch Accounts

To switch between accounts, simply change the `account_name` in `config.yaml`:

```yaml
api:
  account_name: 'Hyma'  # Switch to Hyma account
```

## Quick Setup Script

Run the setup script to interactively configure your environment:

```bash
python setup_env.py
```

This will prompt you for:
1. Account name (e.g., Wood, Hyma)
2. API key for that account
3. API secret for that account

## Example Usage

1. **Wood Account**: Set `account_name: 'Wood'` in config.yaml
2. **Hyma Account**: Set `account_name: 'Hyma'` in config.yaml
3. **Custom Account**: Set `account_name: 'MyAccount'` and add `BYBIT_API_KEY_MyAccount` to .env

The bot will automatically load the correct API credentials based on the account name specified in the configuration.
