from django.views.decorators.csrf import csrf_exempt
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.authtoken.models import Token
from rest_framework.views import APIView
from .models import CustomUser
from .serializers import UserBasicInfoSerializer, UserAdditionalInfoSerializer
from rest_framework.authtoken.views import ObtainAuthToken
from rest_framework.permissions import AllowAny, IsAuthenticated
import os, json
from django.core.exceptions import ImproperlyConfigured
from pathlib import Path
from rest_framework_simplejwt.tokens import RefreshToken

class RegisterStepOne(APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        serializer = UserBasicInfoSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            return Response({"user_id": user.id, "message": "Step 1 completed. Proceed to step 2."}, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class RegisterStepTwo(APIView):
    permission_classes = [AllowAny]

    def post(self, request, *args, **kwargs):
        user_id = request.data.get('user_id')  # 프론트에서 전달받은 user_id
        if not user_id:
            return Response({"error": "No user ID provided."}, status=status.HTTP_400_BAD_REQUEST)
        
        # Step 1에서 저장한 사용자 ID로 사용자 조회
        try:
            user = CustomUser.objects.get(id=user_id)
        except CustomUser.DoesNotExist:
            return Response({"error": "User does not exist."}, status=status.HTTP_400_BAD_REQUEST)
        
        serializer = UserAdditionalInfoSerializer(user, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class CustomAuthToken(ObtainAuthToken):
    permission_classes = [AllowAny]

    def get(self, request, *args, **kwargs):
        return Response({"detail": "This endpoint only accepts POST requests."})

    def post(self, request, *args, **kwargs):
        serializer = self.serializer_class(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        user = serializer.validated_data['user']
        token, created = Token.objects.get_or_create(user=user)
        return Response({'token': token.key, 'user_id': user.pk, 'email': user.email})
    
class ProfileUpdateView(generics.UpdateAPIView):
    queryset = CustomUser.objects.all()
    serializer_class = UserAdditionalInfoSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        return self.request.user

BASE_DIR = Path(__file__).resolve().parent.parent
secret_file = os.path.join(BASE_DIR, "secrets.json")

with open(secret_file) as f:
    secrets = json.loads(f.read())

def get_secret(setting, secrets=secrets):
    try:
        return secrets[setting]
    except KeyError:
        error_msg = "Set the {} environment variable".format(setting)
        raise ImproperlyConfigured(error_msg)

GOOGLE_SCOPE_USERINFO = get_secret("GOOGLE_SCOPE_USERINFO")
GOOGLE_REDIRECT = get_secret("GOOGLE_REDIRECT")
GOOGLE_CALLBACK_URI = get_secret("GOOGLE_CALLBACK_URI")
GOOGLE_CLIENT_ID = get_secret("GOOGLE_CLIENT_ID")
GOOGLE_SECRET = get_secret("GOOGLE_SECRET")


from django.shortcuts import redirect
from json import JSONDecodeError
from django.http import JsonResponse
from allauth.socialaccount.models import SocialAccount, SocialToken, SocialApp
import requests
from django.contrib.auth import get_user_model

# 로그인 페이지 연결
def google_login(request):
   scope = GOOGLE_SCOPE_USERINFO        # + "https://www.googleapis.com/auth/drive.readonly" 등 scope 설정 후 자율적으로 추가
   return redirect(f"{GOOGLE_REDIRECT}?client_id={GOOGLE_CLIENT_ID}&response_type=code&redirect_uri={GOOGLE_CALLBACK_URI}&scope={scope}")


User = get_user_model()

# 인가 코드를 받아 로그인 처리
@csrf_exempt
def google_callback(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            id_token = data.get('id_token')
        except json.JSONDecodeError:
            return JsonResponse({"status": 400, "message": "Invalid JSON"}, status=400)

        if not id_token:
            return JsonResponse({"status": 400, "message": "ID Token not provided"}, status=400)
        
        # Google API를 통해 ID Token 검증
        token_info_url = f"https://www.googleapis.com/oauth2/v3/tokeninfo?id_token={id_token}"
        token_info_res = requests.get(token_info_url)
        
        if token_info_res.status_code != 200:
            return JsonResponse({"status": 400, "message": "Invalid ID Token"}, status=400)
        
        token_info = token_info_res.json()
        email = token_info.get("email")

        if not email:
            return JsonResponse({"status": 400, "message": "Email not found in ID Token"}, status=400)

        try:
            user, created = User.objects.get_or_create(email=email, defaults={'username': email.split('@')[0]})
            
            if created:
                # New user was created, you might want to handle additional registration logic here
                user.set_unusable_password()  # Or set a default password
                user.save()
            
            # Find or create social account for the user
            social_user, created = SocialAccount.objects.get_or_create(user=user, provider='google')

            if created:
                social_app = SocialApp.objects.get(provider='google')
                SocialToken.objects.create(app=social_app, token=id_token, token_expiry=None, account=social_user)
            
            # Generate JWT tokens
            refresh_token = str(RefreshToken.for_user(user))
            access_token = str(RefreshToken.for_user(user).access_token)
            
            return JsonResponse({
                "user": {
                    "id": user.id,
                    "email": user.email,
                },
                "message": "Login successful",
                "token": {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                },
            }, status=200)

        except Exception as e:
            return JsonResponse({"status": 400, "message": str(e)}, status=400)
    else:
        return JsonResponse({"status": 405, "message": "Method not allowed"}, status=405)