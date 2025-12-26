---
description: How to build and push Docker image to AWS ECR
---

# Push Docker Image to ECR

## Prerequisites
- AWS CLI configured with appropriate credentials
- Docker installed and running

## Steps

### 1. Set variables
```bash
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION=ap-southeast-2
REPO=library-auth-service
ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${REPO}"
```

// turbo
### 2. Login to ECR
```bash
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin ${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com
```

### 3. Build the image
```bash
docker build -t ${REPO}:latest .
```

### 4. Tag for ECR
```bash
docker tag ${REPO}:latest ${ECR_URI}:latest
```

### 5. Push to ECR
```bash
docker push ${ECR_URI}:latest
```

## Quick One-Liner (after login)
```bash
docker build -t library-auth-service . && \
docker tag library-auth-service:latest ${ECR_URI}:latest && \
docker push ${ECR_URI}:latest
```

## ECS
```bash
aws ecs update-service \
  --cluster library-auth-worker-cluster-rl \
  --service library-auth-service-worker-service-gne5k8x3 \
  --force-new-deployment \
  --region ap-southeast-2
```

## Notes
- Make sure the ECR repository exists before pushing
- Create repo if needed: `aws ecr create-repository --repository-name library-app-auth-service --region ap-southeast-2`



## clamav:

```bash
cd clamav
docker build -t clamav-tcp .
docker tag clamav-tcp:latest 681802564174.dkr.ecr.ap-southeast-2.amazonaws.com/clamav-tcp:latest
docker push 681802564174.dkr.ecr.ap-southeast-2.amazonaws.com/clamav-tcp:latest
```


```bash
aws ecs update-service \
  --cluster library-auth-worker-cluster-rl \
  --service clamav-service-service-018u4hk3 \
  --force-new-deployment \
  --region ap-southeast-2
```