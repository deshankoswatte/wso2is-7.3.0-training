/*
 * Copyright (c) 2015, WSO2 Inc. (http://www.wso2.org) All Rights Reserved.
 *
 * WSO2 Inc. licenses this file to you under the Apache License,
 * Version 2.0 (the "License"); you may not use this file except
 * in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing,
 * software distributed under the License is distributed on an
 * "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
 * KIND, either express or implied.  See the License for the
 * specific language governing permissions and limitations
 * under the License.
 *
 * Training version of the WSO2 sample MobileGrant: the mobile number must belong
 * to an existing user (Mobile attribute), and Identity Server issues a normal access token.
 */

package org.wso2.sample.identity.oauth2.grant.mobile;

import org.apache.commons.logging.Log;
import org.apache.commons.logging.LogFactory;
import org.wso2.carbon.identity.application.authentication.framework.model.AuthenticatedUser;
import org.wso2.carbon.identity.core.util.IdentityTenantUtil;
import org.wso2.carbon.identity.oauth2.IdentityOAuth2Exception;
import org.wso2.carbon.identity.oauth2.ResponseHeader;
import org.wso2.carbon.identity.oauth2.model.RequestParameter;
import org.wso2.carbon.identity.oauth2.token.OAuthTokenReqMessageContext;
import org.wso2.carbon.identity.oauth2.token.handlers.grant.AbstractAuthorizationGrantHandler;
import org.wso2.carbon.user.core.common.AbstractUserStoreManager;
import org.wso2.carbon.user.core.common.User;

import java.util.List;
import java.util.regex.Pattern;

public class MobileGrant extends AbstractAuthorizationGrantHandler {

    private static final Log log = LogFactory.getLog(MobileGrant.class);

    public static final String MOBILE_GRANT_PARAM = "mobileNumber";
    private static final String MOBILE_CLAIM = "http://wso2.org/claims/mobile";
    // 10 digits, with an optional country code such as +94.
    private static final Pattern MOBILE_PATTERN = Pattern.compile("^(\\+\\d{1,3})?\\d{10}$");

    @Override
    public boolean validateGrant(OAuthTokenReqMessageContext tokReqMsgCtx) throws IdentityOAuth2Exception {

        log.info("Mobile Grant handler is hit");

        // 1. Read the mobileNumber parameter from the token request.
        String mobileNumber = null;
        for (RequestParameter parameter : tokReqMsgCtx.getOauth2AccessTokenReqDTO().getRequestParameters()) {
            if (MOBILE_GRANT_PARAM.equals(parameter.getKey()) && parameter.getValue() != null
                    && parameter.getValue().length > 0) {
                mobileNumber = parameter.getValue()[0];
            }
        }
        if (mobileNumber == null || !MOBILE_PATTERN.matcher(mobileNumber).matches()) {
            return reject(tokReqMsgCtx, "Provided Mobile Number is Invalid.");
        }

        // 2. Find the user whose Mobile attribute has this number.
        String tenantDomain = tokReqMsgCtx.getOauth2AccessTokenReqDTO().getTenantDomain();
        List<User> users;
        try {
            int tenantId = IdentityTenantUtil.getTenantId(tenantDomain);
            AbstractUserStoreManager userStoreManager = (AbstractUserStoreManager) IdentityTenantUtil
                    .getRealmService().getTenantUserRealm(tenantId).getUserStoreManager();
            users = userStoreManager.getUserListWithID(MOBILE_CLAIM, mobileNumber, null);
        } catch (org.wso2.carbon.user.api.UserStoreException e) {
            throw new IdentityOAuth2Exception("Error while searching for the user by mobile number.", e);
        }
        if (users == null || users.size() != 1) {
            log.info("No single user found with mobile number " + mobileNumber);
            return reject(tokReqMsgCtx, "No user found for the mobile number.");
        }

        // 3. Set that user as the user of the token.
        User user = users.get(0);
        AuthenticatedUser authenticatedUser = new AuthenticatedUser();
        authenticatedUser.setUserId(user.getUserID());
        authenticatedUser.setUserName(user.getUsername());
        authenticatedUser.setUserStoreDomain(user.getUserStoreDomain());
        authenticatedUser.setTenantDomain(tenantDomain);
        authenticatedUser.setAuthenticatedSubjectIdentifier(user.getUsername());
        tokReqMsgCtx.setAuthorizedUser(authenticatedUser);
        tokReqMsgCtx.setScope(tokReqMsgCtx.getOauth2AccessTokenReqDTO().getScope());
        log.info("Mobile number " + mobileNumber + " belongs to user " + user.getUsername());
        return true;
    }

    private boolean reject(OAuthTokenReqMessageContext tokReqMsgCtx, String message) {

        ResponseHeader responseHeader = new ResponseHeader();
        responseHeader.setKey("SampleHeader-999");
        responseHeader.setValue(message);
        tokReqMsgCtx.addProperty("RESPONSE_HEADERS", new ResponseHeader[]{responseHeader});
        return false;
    }

    @Override
    public boolean authorizeAccessDelegation(OAuthTokenReqMessageContext tokReqMsgCtx) {

        return true;
    }

    @Override
    public boolean validateScope(OAuthTokenReqMessageContext tokReqMsgCtx) {

        return true;
    }

    @Override
    public boolean isOfTypeApplicationUser() throws IdentityOAuth2Exception {

        return true;
    }
}
