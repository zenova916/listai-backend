"""
services/email_service.py
Sends transactional emails via Resend (free — 3,000/month).
"""
import os, resend

resend.api_key = os.getenv("RESEND_API_KEY", "")
FROM = os.getenv("FROM_EMAIL", "onboarding@resend.dev")
APP_URL = os.getenv("API_URL", "http://localhost:8000")
FRONTEND = os.getenv("FRONTEND_URL", "http://localhost:3000")


def send_verification_email(to: str, name: str, token: str):
    verify_url = f"{APP_URL}/auth/verify-email?token={token}"
    try:
        resend.Emails.send({
            "from": f"ListAI <{FROM}>",
            "to": [to],
            "subject": "Verify your ListAI email",
            "html": f"""
            <div style="font-family:sans-serif;max-width:520px;margin:0 auto;">
              <h2 style="color:#1a56db;">Welcome to ListAI, {name}!</h2>
              <p>Click the button below to verify your email and activate your free account.</p>
              <p>You get <strong>5 free listings</strong> to start — no credit card needed.</p>
              <a href="{verify_url}"
                 style="display:inline-block;background:#1a56db;color:#fff;
                        padding:14px 28px;border-radius:8px;text-decoration:none;
                        font-weight:bold;margin:20px 0;">
                Verify my email →
              </a>
              <p style="color:#888;font-size:12px;">
                If you didn't sign up, ignore this email.
              </p>
            </div>
            """
        })
    except Exception as e:
        print(f"[Email] Failed to send verification to {to}: {e}")


def send_welcome_email(to: str, name: str):
    dashboard_url = f"{FRONTEND}/dashboard"
    try:
        resend.Emails.send({
            "from": f"ListAI <{FROM}>",
            "to": [to],
            "subject": "Your ListAI account is ready 🎉",
            "html": f"""
            <div style="font-family:sans-serif;max-width:520px;margin:0 auto;">
              <h2 style="color:#1a56db;">You're all set, {name}!</h2>
              <p>Your ListAI account is verified and ready to go.</p>
              <p><strong>Here's how to create your first listing:</strong></p>
              <ol style="line-height:2;">
                <li>Connect your eBay account (takes 60 seconds)</li>
                <li>Type a product title or upload a CSV</li>
                <li>Review the AI-generated listing</li>
                <li>Hit publish — it goes live on eBay instantly</li>
              </ol>
              <a href="{dashboard_url}"
                 style="display:inline-block;background:#1a56db;color:#fff;
                        padding:14px 28px;border-radius:8px;text-decoration:none;
                        font-weight:bold;margin:20px 0;">
                Go to my dashboard →
              </a>
            </div>
            """
        })
    except Exception as e:
        print(f"[Email] Failed to send welcome to {to}: {e}")


def send_plan_activated_email(to: str, name: str, plan: str):
    quotas = {"starter": "50", "pro": "500", "agency": "Unlimited"}
    quota = quotas.get(plan, "5")
    try:
        resend.Emails.send({
            "from": f"ListAI <{FROM}>",
            "to": [to],
            "subject": f"ListAI {plan.title()} plan activated ✅",
            "html": f"""
            <div style="font-family:sans-serif;max-width:520px;margin:0 auto;">
              <h2 style="color:#1a56db;">Your {plan.title()} plan is live!</h2>
              <p>Hi {name}, your payment was received and your plan is now active.</p>
              <p>You now have <strong>{quota} listings per month</strong>.</p>
              <p>Your quota resets on the same date every month.</p>
              <a href="{FRONTEND}/dashboard"
                 style="display:inline-block;background:#1a56db;color:#fff;
                        padding:14px 28px;border-radius:8px;text-decoration:none;
                        font-weight:bold;margin:20px 0;">
                Start listing →
              </a>
            </div>
            """
        })
    except Exception as e:
        print(f"[Email] Failed to send plan email to {to}: {e}")
