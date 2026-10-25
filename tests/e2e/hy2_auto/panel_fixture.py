"""Owned VM128 panel fixture for isolated Hysteria2 automatic UDP forwarding tests.

All application output, including initial admin credentials and seed output,
stays under the Root-only private directory. Public stdout is fixed metadata.
"""
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import socket
import sqlite3
import stat
import subprocess
import sys
import time
import tarfile
import shutil

sys.dont_write_bytecode = True
os.umask(0o077)
BASE = Path('/opt/xboard-hy2-auto-panel-20261025')
PRIVATE = BASE / 'private'
NAME = 'codex-xboard-hy2-auto-panel-20261025'
VOLUME = NAME + '-redis'
IMAGE = 'sha256:f8d45147fe23db138f0922b6137d162b283f90c2129ca6775c507ba084e00d73'
PORT = 17006
ARCHIVE_SHA = '36d1fd4b832e1e3473836512a1528c5f1f62764b358af1c96f19d7be999bfa79'
FIX_FILES = {'app/Console/Commands/BackupDatabase.php': '420539ba5bdfc3fad48aba5bf41bf1d618e49f281053ce2acd759bfdc36ab310', 'app/Console/Commands/BundledPlugins.php': '772cb5a445239e1f8486f13f242c8b2b0a5043c05c2459b3a49b8eb3a0a19bc9', 'app/Console/Commands/CheckCommission.php': 'dff5a6d759c3f88e617dcc663324c331f662325f7f49ae86bbcce70a74a37e95', 'app/Console/Commands/CheckOrder.php': '08988119c17bb8836f111125e562067a911d49829edee71fd47ad72d5e17e594', 'app/Console/Commands/CheckServer.php': 'f900cc97476da11bc461d4484c4ad63fc2cf59b07d2213c7a6536e3b10d99c47', 'app/Console/Commands/CheckTicket.php': '5af3941f2f2a1c5bfc11cfa0e6559183ce57bb374f205901510211b3d792561f', 'app/Console/Commands/CheckTrafficExceeded.php': 'ae61863f3b2d7fafb59d2565f84060cd707d54ec332bce08c5309704d932595a', 'app/Console/Commands/CleanupOnlineStatus.php': 'd656002ebc22eed4b0fb8527e29aa18814feec98a832a10b8a2369d401ec95c3', 'app/Console/Commands/ClearUser.php': '45e45d5aca95b8da506fa097f233692e1d6ac94b2dd64eb6486e61d73e2df779', 'app/Console/Commands/ConfigureEndpoint.php': '1d5fd2651c51691243df275f5f65d923fcfcb4c241980ea3c526694084469c75', 'app/Console/Commands/HookList.php': 'a3894af8bc9d61eb4a3b027206e020db2be99f6ed18effbc38f0c28f7fefc4d0', 'app/Console/Commands/MigrateFromV2b.php': '532c2de9a513218c583da0120981745d080137cd25f80bf83906e5c7f8f8de7a', 'app/Console/Commands/NodeWebSocketServer.php': '49a1fc38a024046ced6909e0001af83f8da617f6018bec7cf68042a5f35345ca', 'app/Console/Commands/ResetLog.php': '52c521848183c33e1da1f5bfe140f95b7ce1b931912ce7df63f0d82402ae606c', 'app/Console/Commands/ResetPassword.php': 'c6278cd65481b9c45465a71edb428d15b4aeb6dc471c4c692b1a2984bbaff73d', 'app/Console/Commands/ResetTraffic.php': '8e0f6cebbdd30f749073236d1629d023a5e245ef86ba82cdd4e5fcd5e7d3b2c3', 'app/Console/Commands/ResetUser.php': '5d063da3ee341ee6daa8330b9ebdfbd645fe0770dad4626751b3e629b325938d', 'app/Console/Commands/SendRemindMail.php': '5adc2d33feb501ce744f12857b85ab87b35a6ef632eb347885bda501bbf864e3', 'app/Console/Commands/Test.php': '9c42131ee104f505e537b62b3e2e82ed9ca7bed8e2d179d08b3d77d830222a28', 'app/Console/Commands/XboardInstall.php': '6738cf268faeb5d021168521659b9a98d1c1bdc5760477ff04c95c292157209a', 'app/Console/Commands/XboardRollback.php': '21b04f5b9d82cd28de69485fe30137b5979c5ef6d64f2c2b5ad72f1ceb0bbf03', 'app/Console/Commands/XboardStatistics.php': '3f58071e45619c7f74bddaab2f6216f04160da6f3b72b975ab4e4d02c37d4c74', 'app/Console/Commands/XboardUpdate.php': '2e0de1eb5b261cea944af31020fe9070be891912195c29b1bde9e0859d377785', 'app/Console/Kernel.php': '41235b2fd2e05a9e84c127c94a0ffcca272edc3fefbe7e6acc0ffe68a2009808', 'app/Contracts/PaymentInterface.php': 'f64435689925289eb9cc0d25312febadc2e614a2b053b6ab1acbbf836eaafd55', 'app/Exceptions/ApiException.php': '047972187fe7dc00823553dff72385eda3214bccdb5d2177a926e4efa07d9738', 'app/Exceptions/BusinessException.php': '9a299df30c403be09f9548a6a357287ff92d8acab676ce5b3c477c9f73fda99d', 'app/Exceptions/Handler.php': '28b1eb14acb2103142b08cc91d6783208013bdb07fe3ccb864abf742e8578789', 'app/Helpers/ApiResponse.php': '149873c8fa031c9f54b824f26b120981620fb96c9dcb52c1f916f30f96ab0ddf', 'app/Helpers/Functions.php': 'ef211a4ecf3fee60311c2ba3f2d22a2509ecd24afe9861ca47808ff12a044a64', 'app/Helpers/ResponseEnum.php': 'f30d8893b58695a38907726a3418f6a7d2aa8d0807ac966ef6048eadea77cab2', 'app/Http/Controllers/Controller.php': 'b26dd80d0cf40a5dbb30ed961d02e5023b2ac0c0d406d83cb883b8b49657a2b5', 'app/Http/Controllers/NodeInstallerAssetController.php': '23f85d3d97005f028ed85d437ebfc43a5c8d8514ea8c1eb61775cfb486b5aa30', 'app/Http/Controllers/PluginController.php': '7f3386f6864c74d76d6b9f8512477b22c89469af8911ae2aa4512793be8ec78c', 'app/Http/Controllers/PrivateUpdateReleaseController.php': 'a27f8389099ad2a7cc52a229e7b7fd443d904c6da498b61a694ae72014e48cdf', 'app/Http/Controllers/V1/Client/AppController.php': '633bb75a67700d17ce3d3af11391448f53538498b6acbe1c2c2873f1cc906383', 'app/Http/Controllers/V1/Client/ClientController.php': '086f66110e3d5e112e78c24a09c2f1d3832b185c7df646aca3cdc96d7f6b7322', 'app/Http/Controllers/V1/Guest/CommController.php': '3600ea62cb8b01d6edc10e2773d42b9b01121e3dc9233f99a7565c5f23e5cd5a', 'app/Http/Controllers/V1/Guest/PaymentController.php': '62cad5fac341112c8c53dd4f874c1608d7bb1b83e750a2d7f7ef78d72ebd4e61', 'app/Http/Controllers/V1/Guest/PlanController.php': '569da6cc31cb624e09d586b008228d663f2e8cce08066811114373c3978588dd', 'app/Http/Controllers/V1/Guest/TelegramController.php': '3ec22b93685f2e26625e50ffadf0148fd2b4e0b6176cca417a66b4f1999806bf', 'app/Http/Controllers/V1/Passport/AuthController.php': '0f9254a1609cb29dc892a1f9b7cd2267e7056c2debccdf65ee3e0fab87f90991', 'app/Http/Controllers/V1/Passport/CommController.php': '87303213513e99b3ee19fe53444f5b2efc7f3cba24081805d07c72b0f2e6b33b', 'app/Http/Controllers/V1/Server/ShadowsocksTidalabController.php': 'ce6e1c1fdbfaf1d9de35cc4d001c27e2ad6f12bca54e7261085470ea73bcf2b1', 'app/Http/Controllers/V1/Server/TrojanTidalabController.php': 'c0f065ff1c43a883de2872d0106bc40ab167c0d7f0f3323a20f347844fd45a06', 'app/Http/Controllers/V1/Server/UniProxyController.php': 'f9335920c15f6b433d0f674d02d4b5842d30b8e4e367b74d3b118b5b1a42b4e0', 'app/Http/Controllers/V1/User/CommController.php': 'e6ca343c1ff7744cc08e8cef6f89118d4509c8f878c40a4aba6efd007b7a00f9', 'app/Http/Controllers/V1/User/CouponController.php': 'acf4c8a5610045822118ac55cd2a5f145a6a631c5a4672cbfafa67deb2959b5e', 'app/Http/Controllers/V1/User/GiftCardController.php': '403545039cf3c19290eb3bff91ce89dafda85a08ede6ec193fe2ac5899abe681', 'app/Http/Controllers/V1/User/InviteController.php': '8965e2abe01a4d23c66f17fbf777bae364f9f7d6d9bbc994b8ec98476c9ff29c', 'app/Http/Controllers/V1/User/KnowledgeController.php': '7d54a36cfa2260c2bfbb0363a424e03a8ff396f1673a9b4654c7d23ef43a4e44', 'app/Http/Controllers/V1/User/NoticeController.php': '26a9ca7716f676c8fb135c42ac984142ff4890d5c98e7591977f979161c46180', 'app/Http/Controllers/V1/User/OrderController.php': '9974928dc8b4fa1e56305c80f1dcc98f8d9f544d938f68153a3703e0503a6880', 'app/Http/Controllers/V1/User/PlanController.php': '0f1e81026e11a4c264cdc371b9911d074adb091f83bed83842a71ad90694e5b0', 'app/Http/Controllers/V1/User/ServerController.php': '2f73e3dd965e85f092d373104530f74ddb0b55d3b08043fa95dc44f5763f8ff1', 'app/Http/Controllers/V1/User/StatController.php': '87f91e4af4743e07c4387c78af6b9619104a9d9e665766833b156ce0f9bfe5ae', 'app/Http/Controllers/V1/User/TelegramController.php': '9d4d43b275c91aa43131e7216af0250c8487e466845fa03fbe16e3a6be6d1756', 'app/Http/Controllers/V1/User/TicketController.php': '90fd5f3eb4d13c55c4dfb29f0967c862733b71b7a74c10327ef56a7a2037a1d9', 'app/Http/Controllers/V1/User/UserController.php': '1fab1797e05724f9ce544c9b1c64b0b87d969901bdd9f1859b7814a6f21b69c6', 'app/Http/Controllers/V2/Admin/ConfigController.php': '46f5804a86d811329bc7252f17bd60f9d870a05f0dfed74865e2ca8ce56e4805', 'app/Http/Controllers/V2/Admin/CouponController.php': '507080188b36c254f7874b69c2113c23916846b743b8a884e5a6acaea2b4d563', 'app/Http/Controllers/V2/Admin/GiftCardController.php': '2c5bc544f385ddef8f6274af302fabdc0b452ef4f44b2d8229f6a00d729791be', 'app/Http/Controllers/V2/Admin/KnowledgeController.php': '496836c19a68a5ae653688e85b0902948477db416c76ca3eb7e98c4faf500faa', 'app/Http/Controllers/V2/Admin/MailTemplateController.php': 'e3de7afeb81c758f31a700ca7b19228ecc8a153a92620bfe4025e8e352c41035', 'app/Http/Controllers/V2/Admin/NoticeController.php': '7708caae8d1efa8b8b61dae541e5c5834011c35a33d649b795ca1eb40512edf9', 'app/Http/Controllers/V2/Admin/OrderController.php': '4709c8e272e8a58f43e92841a941992d344bf7eec676455ff8c7207b3ec4a50d', 'app/Http/Controllers/V2/Admin/PaymentController.php': 'f093a2404b944d68c0ca4756dad507511596b179c16de52256f33ca78cc6b0c7', 'app/Http/Controllers/V2/Admin/PlanController.php': '3635dadfd4bd9873834f636497eefa720c21bd43d2cd3146284084369d359d4e', 'app/Http/Controllers/V2/Admin/PluginController.php': 'b37965472c19e1852cc4266999a30403eb1ca918d0e14b1382dda0d16383420e', 'app/Http/Controllers/V2/Admin/Server/GroupController.php': 'e8d072165ce562793ea1e577203cf515eb7f24907c64e4dff3bb792ac983a2b8', 'app/Http/Controllers/V2/Admin/Server/MachineController.php': '69967f8dad6ee131644d9f99c7122e10de7c1985ffe959e005e3b3a5bb8c7f8c', 'app/Http/Controllers/V2/Admin/Server/ManageController.php': '134bb27a2943b610afe87a6c5810517b3d7eea9cb733ce604e1eb0402fbce105', 'app/Http/Controllers/V2/Admin/Server/RouteController.php': '3b482564cc16710d3eab3b49ee593f39ed8938307038af2dbf406be869ec55a0', 'app/Http/Controllers/V2/Admin/StatController.php': 'a297f7797cf3356a367ca5ebf4187655fdd59b5c289ec0230916dabdfd17c4d5', 'app/Http/Controllers/V2/Admin/SystemController.php': '105bd308517e0ad7877a82d4c76c6f9b5489d29a90789aa65b2396e19d738c5d', 'app/Http/Controllers/V2/Admin/ThemeController.php': '6ed7430b94d46b9361842762a47e625f4f2bdb65deaf2f65c65b3f3a93c0bee7', 'app/Http/Controllers/V2/Admin/TicketController.php': 'acf502ffd7acd2551b227a1b9580238ba1a7828eed2787941506be8739a894f5', 'app/Http/Controllers/V2/Admin/TrafficResetController.php': '005ee52610ae8beb8ac7fd47e378697c9ea0ffe257e855f827d504d6c6152d24', 'app/Http/Controllers/V2/Admin/UpdateController.php': 'b97f571fb82e0950bacaec54e21ed0aedd96e54fec93aeb5780fa8337d3326ea', 'app/Http/Controllers/V2/Admin/UserController.php': 'b688927f53b9ef4b11aa1cdc8d2ffbb2bc3c049262fc624b93f9fc0e05046e71', 'app/Http/Controllers/V2/Client/AppController.php': 'e2212feb0391b46a58bb009be6cf0510ac090a9a6e59bf58f2ec58832f0354d5', 'app/Http/Controllers/V2/Server/MachineController.php': '3d77e17395bbed1d930adbcb2e1436f660ae6a1246bb24d6a4f02f4aaf2b459d', 'app/Http/Controllers/V2/Server/ServerController.php': '8d5180b83e708909ad7bbd3b2852bca6871c3972f57eff83f88ea07267eb44c0', 'app/Http/Kernel.php': '12e81727c485a027692c54e265e53ff30894407cf5d2c053d92ffc25631e82f8', 'app/Http/Middleware/Admin.php': '3d8e937475e82307263a43b7970335427c64c6b80cd2b4d39bcb6db87ff464ad', 'app/Http/Middleware/ApplyRuntimeSettings.php': '907c0598c7392dab73cb6b945baa0a63c0a8b90887cbca7ff93391eb16d618a0', 'app/Http/Middleware/Authenticate.php': 'be881dabcaa34088cf1668cb2f0f1fb11e4bbea890f3dd93e024b2f929162551', 'app/Http/Middleware/CheckForMaintenanceMode.php': '974ee4642fb44f1dc22fd03b9982dc0c373de5c663c38c4b5f9ffb46d6095f73', 'app/Http/Middleware/Client.php': '8398a4bccf3bb08317e0a93d3ad4a39c12d37c6bfb101fc2478a7880d5a86daf', 'app/Http/Middleware/EncryptCookies.php': '53ec5d78c797e344a5fa48a9d003be872c564f9f3109a2ee95e5b6344ddf067e', 'app/Http/Middleware/EnsureTransactionState.php': '17416746ac1d088227f785f407d88ad57ed3736e1040ccd45b168ae29105fa3f', 'app/Http/Middleware/ForceJson.php': '70deaa44aff755c176b58088ab018d332c43be3fe159a58dce9d8dd87d74bcd8', 'app/Http/Middleware/InitializePlugins.php': '5f1040b39ecb22f3a12241c0278de1db5c52ed80a358add112790842ba8471dc', 'app/Http/Middleware/Language.php': '43d17fad91b0e02fc91aa434e17bcbc6def283025d99bfe0561fc0ea0c9a6757', 'app/Http/Middleware/RedirectIfAuthenticated.php': '04a9ac8b2c58339dee1f1b0e297181d6abc79df5b39327d9e28408f5212fd3e3', 'app/Http/Middleware/RequestLog.php': 'b9b46de1b0f6bfc1ba5431384994310e0b10617de7f0148c4ae0f66c51c1e322', 'app/Http/Middleware/Server.php': '7947a3618697f5786bcd47e2dfca21f6d0e5fd6f6e1b34fd1663b68588cab170', 'app/Http/Middleware/ServerV2.php': '8351e5b1118bc051c8dea5005700e63052164753e984a154a8c06d3fdf541cb2', 'app/Http/Middleware/Staff.php': '91c50f28167e17e9652ea5bacb833f95e0e71791c3c15c091112d3e63bb5d8da', 'app/Http/Middleware/TrimStrings.php': '848ad4f21c9b0d06e07db2a38415c3c59db43f21df79b8d4ddeb29786aa21205', 'app/Http/Middleware/TrustProxies.php': 'f259274043cc68d609bb826d5efac4c2f547c734585dbd43462635304a18f5f3', 'app/Http/Middleware/User.php': '98ec351fa6da279b6e13b854739167def4c3a4f56cfeeee8cc7fd99b1f025177', 'app/Http/Middleware/VerifyCsrfToken.php': '69aee1879e2e781c3117d3cf0bb9715b38724a70c851152c38318547b4630b08', 'app/Http/Requests/Admin/ConfigSave.php': 'e204b0403f5427d62c8866b2563557c6e1e1d33d4733949bd7c98f512c898880', 'app/Http/Requests/Admin/CouponGenerate.php': 'a498c6bc447d737d3fa1ce043f0ba0807ade4360c6d6b29a7817ba18c625bf11', 'app/Http/Requests/Admin/KnowledgeCategorySave.php': '02d3be619383c6243c5e5f5cb0074b6f86a64e99f3ca01d51e4e4ab7c569cd93', 'app/Http/Requests/Admin/KnowledgeCategorySort.php': 'a53ca5afbdc625bfee2048d0f9c5674b1f5a517fb546a1d9bc78462b6adadcfd', 'app/Http/Requests/Admin/KnowledgeSave.php': 'b79a451e1565238579275c0cd15db6a7c615c6a695143d2974010a6af0f36279', 'app/Http/Requests/Admin/KnowledgeSort.php': '2582f61f1a94c5b55fdb01dc1a198e4f90f708fdf875339075622c09597a1537', 'app/Http/Requests/Admin/MailSend.php': 'e5ecada618a7d243eabe71cf051d1ec07ab71bcafeffdd5a2b81403ee4d33958', 'app/Http/Requests/Admin/NoticeSave.php': '626aa8c3536cefbb5ad3d661d0b569cac69ad8e52f92c344fe31214e969dcc31', 'app/Http/Requests/Admin/OrderAssign.php': 'a33e2182e820578dc4941ea55fbefd6ab07e76750a5a78bfa0671f50df21b63c', 'app/Http/Requests/Admin/OrderFetch.php': '82d39614dd711f23d07c352ca766541ab8379eed43932ff55eda948fec3a2f23', 'app/Http/Requests/Admin/OrderUpdate.php': '6ab91cc5f0907aae9688c48d6823fe73f055c0ed56bfb9bfbb0f4b925d64793e', 'app/Http/Requests/Admin/PlanSave.php': '1187a739d3ec8af95b932e7fc2aed1f4e6c74854e9159b0b6b63696f7e736e67', 'app/Http/Requests/Admin/PlanSort.php': '60ffaf1b96c9b4d9e201b2211bfb193e8dcfa12f8344e98ac0de1022ddf6dc0c', 'app/Http/Requests/Admin/PlanUpdate.php': '4e61d47beadf641f1b80944f1c3dc2bbee35c00e814c7648bc1deef124365352', 'app/Http/Requests/Admin/ServerSave.php': 'c5884155cb8ad0a0e4d2ef2da864e14090974d5126402dfd83933a4bc7ebd573', 'app/Http/Requests/Admin/UserFetch.php': '1263b2fd0f04c7829ea1e7de656874542eb64e4e1a9bd65bee39282cf2d36b4d', 'app/Http/Requests/Admin/UserGenerate.php': '2171c61edfd7b424b8d21f804299beb2c8aa03178d93535d6bafb5ecdaf997b4', 'app/Http/Requests/Admin/UserSendMail.php': 'e7dddfb142ed7c475f5e8059303ac1fe5e8e6728852444ddc757ac156e0ad369', 'app/Http/Requests/Admin/UserUpdate.php': 'd489ef9fa503f9c560f615b36e7d917390b200ae08a189cfa778828bc0955765', 'app/Http/Requests/Passport/AuthForget.php': '87a0f013e1b34d652a7fc720b2e3d9456eda0aa26916e096eb2dd1984840e383', 'app/Http/Requests/Passport/AuthLogin.php': 'd9623dbe46ce68d662e10dffe52f133608bc3a86f806a9df76ca400d04ce3485', 'app/Http/Requests/Passport/AuthRegister.php': 'b9be6b1c5d1f5e4d5b710aad539965fc9c1be767a7db0bbc55fec756b8faaef7', 'app/Http/Requests/Passport/CommSendEmailVerify.php': 'ffb48c5d1ad2e1f0765f72e3c4128a7a59ed830ab5a56d00216f99058f5588ea', 'app/Http/Requests/Staff/UserUpdate.php': '77bb3eb25bdf92b7e4a35314138922b2e023f0a1599f6e71665df99ece0595fa', 'app/Http/Requests/User/GiftCardCheckRequest.php': 'bb53941f56a3e979e5eb95481c7fdf106f2bcf71993903ed2ecc2fa00bce49ef', 'app/Http/Requests/User/GiftCardRedeemRequest.php': '271f73a23d96daad89628011823fc922e7c6fe1665d501fbae46f45c4f7668bf', 'app/Http/Requests/User/OrderSave.php': '20d5d3af969d00062c388bc388ce2c2a447fa00fbf9f12cb9e46f25490b747c0', 'app/Http/Requests/User/TicketSave.php': 'e33123eddb582fb712c39f0dbfc3cf683322ea882548c55d09a558e253ec203c', 'app/Http/Requests/User/TicketWithdraw.php': 'f5785d904f2be0722cbc536753618f739388ef6835a8c49ce203ba858769ebe6', 'app/Http/Requests/User/UserChangePassword.php': 'a5e5c053db732fb5dd34a6b16643886167811bba1c3250681dfbf3024c5c7821', 'app/Http/Requests/User/UserTransfer.php': '19cc8d147b1069539998ac85514d7cffb4aca5594ce17d016a6f55c2a1290533', 'app/Http/Requests/User/UserUpdate.php': 'c02e24d534d1b7b72fd9577267dcf4998ccb98b9ce3f1cfce1f6103c5ac048d8', 'app/Http/Resources/ComissionLogResource.php': '56d39e5a1fbb12f44014cb4d20bc69e8088d799feb3e5dbd676b61e1f6ce010a', 'app/Http/Resources/CouponResource.php': '0142b33a4255a07c50378af238991f2d62993b0c8aa3ae78e041a0c4c758cd49', 'app/Http/Resources/InviteCodeResource.php': 'bca5782ec5db7361722cfa536d7e424c9356a0e1c946a5dfe62abcf98186ee03', 'app/Http/Resources/KnowledgeResource.php': 'd3dcb024b7bbf7c3a584df920666f1b38e278e9241e8b1e695d27afa30b0c8be', 'app/Http/Resources/MessageResource.php': '3e47cdb527221a179416aea06476afd5fea6a177bdb5f147a4a1171491cc66e3', 'app/Http/Resources/NodeResource.php': 'd9978a192d2c54ae18401a417948dd237fb787cf1baf5fc7527855ee8432871b', 'app/Http/Resources/OrderResource.php': '02c5642a030a0ceb35768ffbacaf78ee85a5e0ce8a8ee1b3ee561da7ea432783', 'app/Http/Resources/PlanResource.php': '963df4f934c4613336114cc94967cfeb332c7dca123587ad38fdd208d714be81', 'app/Http/Resources/TicketResource.php': 'ba04466f00d1d4fcf1a5eda2f42d3d3e4684a9cf384d15459cdd5ce9da6f697d', 'app/Http/Resources/TrafficLogResource.php': '49031348b182cf3616c93db5316c32307e4308a336eb2a72271598de43299724', 'app/Http/Routes/V1/ClientRoute.php': '7042863cb6300ca388bf9d6043385f5ad774205641abe61b5403ef3521fc0000', 'app/Http/Routes/V1/GuestRoute.php': '411f9c74f5823e63b32aeccd1323d5c866071d3bac9811ec225335543c0ef41a', 'app/Http/Routes/V1/PassportRoute.php': '8a7de5438a24900ca47cd7679886859e37eb1d68e26ae80d4d3672a54b5e1b86', 'app/Http/Routes/V1/ServerRoute.php': 'ed792a54ef5d1bd98c8e895e675067167f98c93b234296e16fc85c40e1ecdd61', 'app/Http/Routes/V1/UserRoute.php': '691c1a0f2ece286bee48fb564c6cc5ff16d198c9c7370e3d18f27b68afcc0e49', 'app/Http/Routes/V2/AdminRoute.php': '3d3dff1757b1c5c5424169e507d19f2bb3777f40ab2e35426f2861967915f7b4', 'app/Http/Routes/V2/ClientRoute.php': 'eabe0e96ee832171fc339b1928ff2ad7aed59248ed86cb2c87faa75b3379de36', 'app/Http/Routes/V2/PassportRoute.php': '746740439861cff8f01e9ce385ac1bf7921d5c51522e75f84ae1c402eb01ab7b', 'app/Http/Routes/V2/ServerRoute.php': 'eb4829a8955b0752f820074ba89a6c6addab77c60a228cff2058c10caf77bd14', 'app/Http/Routes/V2/UserRoute.php': '069c4f97b3404b43a1d627a4882b2cbe1916836ce787cba1fa5a411c5d09efaa', 'app/Jobs/NodeUserSyncJob.php': '75eb00a8e0528d419295e54387b536fb7143365aa41ac1d7a90708e067f967e1', 'app/Jobs/OrderHandleJob.php': '7cd61e9fe5a90796f889dd294531c2ea5d4050c73252013cdef02c0d33fbe7b0', 'app/Jobs/SendEmailJob.php': '23f06f93d5548dc2917a1a8c4498347d50d69096a9633338537e7e547524dcd4', 'app/Jobs/SendTelegramJob.php': '8f3b444cc4627d8f46f74970fb5b708c02b4e6b2e378de057b3cdb67442319c4', 'app/Jobs/StatServerJob.php': '65b2fa23a9ead4c3cc25b9505065cb8b706df82f10310715c516bf9dc4b6ba9f', 'app/Jobs/StatUserJob.php': '65ca3082afef04c8171b0fa6495d038f1933e4898f14b4bdec2027a95fbe105f', 'app/Jobs/TrafficFetchJob.php': '80a6e1383e0f560bcbebc217aae77664826e48fae1a814710369b92fb7517c89', 'app/Models/AdminAuditLog.php': '152edfd0fe05418238424d062c30dcf62b93be175b0b494de3b929e46387f3cf', 'app/Models/CommissionLog.php': '82cc2b5beeee2d8afb68654a71cc98dfcae59ec6d447701e3c7bc85beaf6a4bc', 'app/Models/Coupon.php': 'ea81aaa5c5f19e421afb587fb7d936a065cbeb123a1bafbcd4996b69f3536375', 'app/Models/GiftCardCode.php': '54608c083944330523d87b6b9c4d8029a7877664eade5801b011745cb8799052', 'app/Models/GiftCardTemplate.php': '9115992b92e962940db583810ef333a23ad6503644e077b1bf8e1b137c51c89d', 'app/Models/GiftCardUsage.php': 'f19fe4a49cb46d82174ec4fc475b462ef1dfe35e16fa52e3c98aa19adc19ecd7', 'app/Models/InviteCode.php': 'e8d8ff54db4fe39eb521bc5f79fdb41627f05bc93a38ff34c3923604dc854e71', 'app/Models/Knowledge.php': 'de998cf6d8209083b219b30c96106d277bd9bf68cdfd9f9d60e868ad87f5f884', 'app/Models/MailLog.php': '1c9229cda148a118a431c5863d03e3524ba815a66735dd8be464a00a37a3f53a', 'app/Models/MailTemplate.php': '95c4093ac4082af2891103785433d6d565f8bf5111ac677d0f8794c149e4db88', 'app/Models/Notice.php': '9cbd28ed2e2a79a1a2ab603b6b1afcc838808b0329fa04504b3431b52d555e5f', 'app/Models/Order.php': '37273d02e213ba9783038bca3def2a69c7d90c4f0a30c0d8b0174e4a15b3f796', 'app/Models/Payment.php': '62e29114625adbe87b6abb1b52729786409c17c8df7d0858e0473fa5b603c44f', 'app/Models/Plan.php': '2c405511ac59316be838fd85a0e57f142ed7b75f25e8a29548a36a32f31498d5', 'app/Models/Plugin.php': '2992d8fac3b9a3f8cc8293e59e40feeeacf41c05a934dc4a87ad3ad7eb89e9a1', 'app/Models/Server.php': '91c51b37a804fdc5bcd4e4803e2c7cbc69d205dd25dfd489d972c3cbdbd967e3', 'app/Models/ServerGroup.php': 'dba303e5e6427616a70436b0e49fc2ae1bb31a7ee9518150527c6b7b2703b767', 'app/Models/ServerLog.php': 'db84370b41e344cf744dcd0cb5404952db75de68f3f4e7621d1bd789359ef01e', 'app/Models/ServerMachine.php': '19148f6a32f91f147fe02ff3e4c2f44db5ca6e6074c515447d709b7a17676b1f', 'app/Models/ServerMachineLoadHistory.php': '5547563e924c2f2179705039913b13da502a5dd5d258df57679e58a94ff9458a', 'app/Models/ServerRoute.php': 'be8dd25c014727e5b0de1b8f07d300b6d7bb1ae84b2f5be85868996a34f3da50', 'app/Models/ServerStat.php': '258da0981f9ebe827b4a3a2c6482c589592882e423b550a4c2a4f323813fa4c3', 'app/Models/Setting.php': 'e9d22a1275dffa4882b4ba7ade03d492170aca7f06cfc66b4f24bc35058a0694', 'app/Models/Stat.php': '6808153b000752ac34ee4bb7fb1638861442bb6ab4fd1cf6562357d88dfd5deb', 'app/Models/StatServer.php': 'c9b50aa807fe0591e87a699e1f6b85caf73171a4de10ffbae1166e58fd5c8782', 'app/Models/StatUser.php': '8ecaa5717f61e919c735e380463059929aeaa28ebc06a0b6e7e2bb837115be46', 'app/Models/SubscribeTemplate.php': '210bb61d829b0c62b61273e0bd0e7d3c738c614da7b2e3eb2c70617a7e27df98', 'app/Models/Ticket.php': '0e032ffc6d4900cfb430c85aaca1208e4d84db5e159e2ba27ec1be18f3faf680', 'app/Models/TicketMessage.php': '4afda30ef1a84a0e05b675a2b51e635d16ce5b82234b1d0c57e3bb0f1d15859c', 'app/Models/TrafficResetLog.php': '700e0712f843fbe23f24c0de403a237e31cdb2aadb6e4b023ca335708806a662', 'app/Models/User.php': 'e1e5275fe0ea89e9b668c6cb49e9fd5567a98cf6fdc2701edad7ed7bb1c848c0', 'app/Observers/PlanObserver.php': 'c5a608927116dfbb466d4c2ca7c106553cb36d6d7868373366d35c9838529c81', 'app/Observers/ServerObserver.php': '2d6d485965c1f80eb1c6690eb27746220fedc08b620b80f6a1f9b8414db10491', 'app/Observers/ServerRouteObserver.php': '90eae4839b903756d9531dc4de000b8c018724d79efdea41ea5f257dad7d5067', 'app/Observers/UserObserver.php': 'a89054fee89cdf6cac115b7dfb736504ec35219e81eac45720f5f374ce44940a', 'app/Protocols/Clash.php': '3dfbb3189513b93a5004e6e979cda1a14102afdbc9a1ee00352801b8a75a6aea', 'app/Protocols/ClashMeta.php': '1e68989a767df5c7f8d0e54d0521d2c03fe81540c370c9bed8df04041074195c', 'app/Protocols/General.php': 'a4ce994be13562ce11ea695b2c55284167f63c73bd5ad02dc8118798c80912f1', 'app/Protocols/Loon.php': 'a6fb3176b7ff0f87b6b9e4d4b34f52b5ff40ef2e223c9aeb73c1f82cda80fa6e', 'app/Protocols/QuantumultX.php': '49d42c73974d049584a5fa1d22f2221f09f018cc15c01ac1c75cf656ec785317', 'app/Protocols/Shadowrocket.php': 'cb565333efb8749f7112e13763c943265986b2ed6449c7f35aa581f8a54ba804', 'app/Protocols/Shadowsocks.php': 'b1996ff1b4cc951ab3d23e301ee0f341571ad4376f92c5602d9045e010fd4188', 'app/Protocols/SingBox.php': '43c84601c404a2ac0f4d1a77e6e58ea496b4862a5e2454aa7e075f2f0d0b42d6', 'app/Protocols/Stash.php': '63590fd5d72eb8745deb493a78bd5ef4f4e3e4f0936332d25e4836024e57b317', 'app/Protocols/Surfboard.php': '6e131c106b997a017d0cef10dc2726e5c452a25ecc71068a2545fd68c51ec3fc', 'app/Protocols/Surge.php': '61a60245d07eea3d18f0f7e0570beff4eeb430938572dab0455a1fb2cdcfd4f6', 'app/Providers/AuthServiceProvider.php': '014e2e003019cbab68e24a2284ae13a891e45e1cb1737a9b39095319da21595c', 'app/Providers/BroadcastServiceProvider.php': '0fb6d864904760e961bd61def79005af1af18d70f32aed6b1c1378bf0a598b64', 'app/Providers/EventServiceProvider.php': '6c12e76314beb2efe4695e772a2cc428a15f1ce7b710539aff354e35c58ae9f6', 'app/Providers/HorizonServiceProvider.php': '7aaff067dc45099ba672994b8f66d941bbcf9819caf64a50efbe82e0a4b5c74a', 'app/Providers/OctaneServiceProvider.php': '349366371eb268e154631d0b3cfec68f68dde5445ae2d77333ed8b5e0a878203', 'app/Providers/PluginServiceProvider.php': '10cb96e3ffef1d62c0c16e0ef37e7778198294a54cb3dcd6ffce4e9f3afc1bc3', 'app/Providers/ProtocolServiceProvider.php': '2e3038fd21a20b7c0d7b5fb9d91113ac5ab4455e7787a8b420eb271dd6d3098a', 'app/Providers/RouteServiceProvider.php': '2bd08ea572f88c3b58440192de805486799f1478ab6353cfe92ff5438f48b01f', 'app/Providers/SettingServiceProvider.php': '4664964c63c9ef177bd6938c05c891c9c40450b29b102a449fe19d1be8fc478e', 'app/Scope/FilterScope.php': '2f68a35495648d64c43e9f71530999e25b047de14627646e86a8164ac665d983', 'app/Services/Auth/LoginService.php': '3cd4e7a64a38f7a46adc0b81c26c8928e827fff3326b008da466c64d80105e56', 'app/Services/Auth/MailLinkService.php': 'e987da36368b5b8f44220926f2be6cd79fa50ad2a8b05a5e6a50288ada563988', 'app/Services/Auth/RegisterService.php': '7ce9329dee0625151523bdd0883ff2b461cb4dc87cdb8f1a1212e775b7075513', 'app/Services/AuthService.php': '9cdeab821569cafca5271ecfaccb30d74949bc734e4fa370d170efe6835c354a', 'app/Services/CaptchaService.php': 'c20d55de93a7833d1b54ae67639f7219a062d8963441e5e4d75077cc89a2e002', 'app/Services/CouponService.php': '91eed43301487246b9dfc94cd6ed69baedd975872e39dd82bfb7f53d40808535', 'app/Services/DeviceStateService.php': '29b17a3f00be50b6c180198dccaab156d21ce54a786e1044eaea971df775c082', 'app/Services/GiftCardService.php': 'c8e182a582226d748a772b5971e74b12382c22136aab2fdbeb3f38f7c45c5357', 'app/Services/MailService.php': 'e800f4c0f4bfdfc56c9eb71bbf7ea3600c618f630baac041a52b25a12e775ccb', 'app/Services/NodeRegistry.php': 'd1aeddf0b1f70a307622bf2f447bdf725fd1dd37e643bf3044f16ebe716b694e', 'app/Services/NodeSyncService.php': '0dd56b0d0c2ed10f49b6dacbe653a9177ce9605fa1b1b7fcd5ad68933955e9f4', 'app/Services/OrderService.php': '4ff7772f8477824f2ab8123a6bde0891bb80b276226b64cb6789daea38a3437c', 'app/Services/PaymentService.php': '1288d9ec9af5bc216b427df08fa273b480c36b2be81e5364325eee366dcdc214', 'app/Services/PlanService.php': 'e67bfecbb220e813a53e75f5daf417d3d6314fb05bb4bf50c9f22410fa97ed4c', 'app/Services/Plugin/AbstractPlugin.php': '60ce2f2c570b5ee1925ab62ee9549927b718e85c7bf39b2298793177b617f8fc', 'app/Services/Plugin/BundledPluginInstaller.php': '63259929646de3c674655e2d287d85878b64a67e5b033de5dc20387bbcfcbbd7', 'app/Services/Plugin/HookManager.php': '29bb5a30c47fa4062dac26c31f8b247888524e13d0d4aeddb1d9e91db1392bde', 'app/Services/Plugin/InterceptResponseException.php': '829aaf783d32f80d645527981c88c843df1335f68247c8f1bc921de3b5f5335a', 'app/Services/Plugin/PluginConfigService.php': 'd7c7918f3f7afcdcead7525cfc605b899fc1f47408b84fb9efee90c0dac8990c', 'app/Services/Plugin/PluginManager.php': '2cc5a6119e68947aa2df1d5eeb39fcbc113e09557a978aed386b4423ee5d01d2', 'app/Services/PrivateArchiveUpdateService.php': '9ef7f279b08d6ffc66ef8c2f922bf95dc491899b711a72b753a0d460315e1c0f', 'app/Services/ServerService.php': '45a553956842a1778a587929360d1324b6a733e26c041397d20e3e016ec3ea40', 'app/Services/SettingService.php': '0a6f4da508c9b5831231d9204412b37583462f86af58116e6f71369fd68718ff', 'app/Services/StatisticalService.php': '00c4d2174453e091bc90764d1753069a947ea6215ee0c50de4ad944d73745ead', 'app/Services/TelegramService.php': 'dddb232995b80d97c046bef254817408a13b3507e7fb38675c7d295b8dafe799', 'app/Services/ThemeService.php': 'f2bf17473834f2e0578c35a86123d6ab01413a85074a97625478eb7b65a4f293', 'app/Services/TicketService.php': '7dfd2dc6814f33d50bcd97ea4cf5670b95e1bc8202771381a1d24ea058aee342', 'app/Services/TrafficResetService.php': 'be10030d7028b3ef7e6f1eda10492e24e5107de49d9cd00692b716a2b2a61a51', 'app/Services/UpdateService.php': 'ed5de899f2cf68e246a40d3768965cf48e801a5e363245a33ad07c721e9e3c2d', 'app/Services/UserService.php': '5ab159c7d755306774edd42dd50c67432b898b4ea3a3b0c8ea0b6b0c150d9139', 'app/Support/AbstractProtocol.php': 'b4e2b210dcdd497510969c70794ebddd69e6f84ad6c41df59705b5b706cef365', 'app/Support/Hysteria2PortHopping.php': '3cddb244705ed015d649e9d92c816474e5efcbf2990256718871d9f720cad811', 'app/Support/ProtocolManager.php': '4e25b28eabb4c3e68395ef312574da46208769445c53d9f8cefc4ac994b08566', 'app/Support/Setting.php': 'e42db65de5c79855f99bddad31c504efcb876f73330d824f019b1d0d76ee04dc', 'app/Traits/HasPluginConfig.php': 'be5db26660bfa83c0f6c69a805a02c971708b13d7a40c17a6245a400e1788f72', 'app/Traits/QueryOperators.php': 'b93460e9378604d5a38fe86fc84d06d8a83d0e01fd6637b656c87acf61a4ed0a', 'app/Utils/CacheKey.php': '4469335c641dba5f226f5dac57caa305e0451c060e7011f6ca46f2b90935557e', 'app/Utils/Dict.php': 'a39dbaea3abe89c28626b530e0b87f7b60f50266c7dbae7f582eb9f0492d76ee', 'app/Utils/Helper.php': 'c66f0b74769402867b4c46f5e5584e31c8e9d6b11132d8cee8e22aef3ba5f2b2', 'app/WebSocket/NodeEventHandlers.php': '8180fa6c813044630a875a43de57cf215719696cda124adb419ad99bb8606d12', 'app/WebSocket/NodeWorker.php': '7c1dff8b1cf6540f8f7e5b8e9e54f4b7b76f41b5d4c2830c31383943af8fe596', 'artisan': 'fc281bf5d4c241469668a21094d32697c449b68c8a67a70a2e83662adaba2ad6', 'bootstrap/app.php': 'c345a3aee9e90f8925e22fd7e7ec5126928797055da0ded6830b283e5cc992f8', 'config/app.php': '4c8094babffdd717009a1b3f2542752829cba7286d9720efbff822c6b17c196a', 'config/auth.php': 'd2723fadfe1bcee386ad73ca86c90115617991260e394b3c03f9b66aa7e6c53f', 'config/broadcasting.php': 'cbedb262e193a3482b14e01f325af3d9596e610b15c4a47ad091a3cb2532a6ec', 'config/cache.php': 'f6fa2f620f2563cc83e700294c0b90103e2ca8d2dd2bd1af3a8b304e2272e029', 'config/cloud_storage.php': '607312e7e5cc9c5f65251cad9fc5cb9335362cd18a80b7930e66d61d1ada02c6', 'config/cors.php': 'b29ab6839086e353d3b487dc624477d8fdeb32791c91276cbbe711fe9fab01ed', 'config/database.php': 'a034a2500608d165ad695a88962d2690812074f513af9968f311868b9489cb7c', 'config/debugbar.php': '70cd8d4b1bce9b9efd90f14a2604e8fec6e60fa3b4394d7d44f3529db8959e50', 'config/filesystems.php': '3283601f8c02148ed4bdb79dbd222246906476820a003982755c0f04eb84d5d7', 'config/hashing.php': 'd8ec83d529107d948e135128426e24acf1ce282039c515fddb136572ce815e93', 'config/hidden_features.php': 'c17930390c242e7e6242528c68355836e2168d850d384e60903333abcce4a680', 'config/horizon.php': 'b9d0894943f6d135c943eeac0fb5f653555668be5f9da2991b28253d279b225f', 'config/logging.php': 'f1817ad14969555948d91f64e398a05273f237204afd52c4ce5f0366eb3f926e', 'config/mail.php': 'f8f2b10e125a639e85d131a2c4469e5b497b60f6c55f7c6b856708966bedbeb7', 'config/octane.php': '2d86bafb265054a84c1170db92e0be78a4f8652325c9c8bac7277869fdd87c9c', 'config/orphan.php': '4420e7aed8ee92bf62a93e850b99c38c6c7c95243940c0e65978ce7e8dfd1261', 'config/queue.php': '2fd9136aec4a33dc8d76d2361fb5811b62f41d8825cf9a7429776ce2186eacff', 'config/sanctum.php': '5d290033ce79c38126855ab73e2858569086ed2f37f82e2f1fcebdce48780185', 'config/scribe.php': '651933739765b7e35c3234b5b8e07bbcc17fd322f340192d576136d41f6ec019', 'config/services.php': '620cd59b6ddfdc14d7c4149a9b4827912b95f973bb77d5a6ed9ac353967831c1', 'config/session.php': 'f78e91de81a8c36f132aa041741bdc8f6f15e442cb538c76dc2def8a638a2a1c', 'config/view.php': 'a264968539852b5f5f6e411bf517cf14cce19b27c31e5e9a1fc0348e016a74b7', 'database/migrations/2019_08_19_000000_create_failed_jobs_table.php': 'fe32b31fe69aea44f4ffa359a2f8bcfc676ee29218872bef8c9d22f9f8ae01a3', 'database/migrations/2019_12_14_000001_create_personal_access_tokens_table.php': '13dcb26b944aee6682bd4a809836efdcf2f88ec18196cb8bb28f61b49939a643', 'database/migrations/2023_03_19_000000_create_v2_tables.php': '7b65a1e26ef7e4c74a36e9d511eabdc5c8b9fc21709bae4163add3e19ba57a74', 'database/migrations/2023_08_14_221234_create_v2_settings_table.php': 'd6be00c8bd06f8432b2d99cb5ee5bfaf518bbe03fbb66fedcf55fb905ec2d487', 'database/migrations/2023_09_04_190923_add_column_excludes_to_server_table.php': 'dce1d83e1247dbb5fe6f64f8ad14000c0973fe9a2ff25d4e2a3d834c06de5c44', 'database/migrations/2023_09_06_195956_add_column_ips_to_server_table.php': 'b6d310a71bb209ee7756b725bdedd60a09bb19f7ca5406a4ae29a579d5a0e913', 'database/migrations/2023_09_14_013244_add_column_alpn_to_server_hysteria_table.php': '3a33266f98b5a5c694ee94fc0a3d6fe005561aa7cff010da7ce8661550aa7d19', 'database/migrations/2023_09_24_040317_add_column_network_and_network_settings_to_v2_server_trojan.php': 'cffd5db8fd4ffdb47723bfb374bf7930d242fe8ab440a71c64a8dc5ec556de52', 'database/migrations/2023_09_29_044957_add_column_version_and_is_obfs_to_server_hysteria_table.php': '3df9d98f657a65de3afb07fd83dd137895d15f796c3980fce4196964b8bf798f', 'database/migrations/2023_11_19_205026_change_column_value_to_v2_settings_table.php': '004a3c13d278613fa1393c62748fed738daf4ba15d4507d65cea8ecb9ed20325', 'database/migrations/2023_12_12_212239_add_index_to_v2_user_table.php': 'bc7ed50efbb0906cef86e945c12d740306e017f1eb2374d180df3276e984c8ee', 'database/migrations/2024_03_19_103149_modify_icon_column_to_v2_payment_table.php': '51d837229a6fc3f7690ea830dee30da1af2967defd7664c65d03ec2002939222', 'database/migrations/2025_01_01_130644_modify_commission_status_in_v2_order_table.php': '6be0dd13bcb9ca8e219706298a829c6242cab5b49c56f7ae4ad4a1566270962d', 'database/migrations/2025_01_04_optimize_plan_table.php': '17a225fa7f8a864cf3c653767d1a9cdb0e42dbfdf1a5f00f3bec6d2837ba114c', 'database/migrations/2025_01_05_131425_create_v2_server_table.php': 'b22625ca323d6f8b2e131ecea2a1b92fb0f95dbe706edd1b3e5c90aea8046dd7', 'database/migrations/2025_01_10_152139_add_device_limit_column.php': '1c060f0aea8992b66792e3be0259cd1e5920e9d0ed62412a606b283b4b6e0248', 'database/migrations/2025_01_12_190315_add_sort_to_v2_notice_table.php': '8f2ea306474c7bde60860527680e2d05847e11d296df92480b9a962f42f512a3', 'database/migrations/2025_01_12_200936_modify_commission_status_in_v2_order_table.php': '4fa7ca0209e94469baf4b2c1a87305474fb4dc7fd7cfb992d99f062736c4b747', 'database/migrations/2025_01_13_000000_convert_order_period_fields.php': 'be3aff9d1287e5b0571a537c376d61375ac028f8c6864f692f7d991939371402', 'database/migrations/2025_01_15_000002_add_stat_performance_indexes.php': '6f54baccfc3ed3fe714995bc0522e90f74c04cd7556b7a025206dd400e2bcc96', 'database/migrations/2025_01_16_142320_add_updated_at_index_to_v2_order_table.php': '72fec572f1e615a9e0ea20fdb666c7cd0c95496a40efcda6651fbd6390084ed7', 'database/migrations/2025_01_18_140511_create_plugins_table.php': '5e06e1cfb6008bc25e9a4ffabc095246f9317a20904a56b822048883a33098a1', 'database/migrations/2025_06_21_000001_optimize_v2_settings_table.php': 'fa0fa49b6b1b28a0c9c8e122048c5a279c65199501ad0684a2f7f0daa13cacd7', 'database/migrations/2025_06_21_000002_create_traffic_reset_logs_table.php': '2f2b722ad42e1ee8f1589c776db1d6eb0a6de00a92a280f843102ed831e0ada6', 'database/migrations/2025_06_21_000003_add_traffic_reset_fields_to_users.php': '7677389cdb21c46b9ec3a72bcb36658866ef905ebc881c521f782f57b5852134', 'database/migrations/2025_07_01_081556_add_tags_to_v2_plan_table.php': 'b99bce95d62a36f61fce5634f0b31ec6d964453fb4017b3ce71b4ea83e1507db', 'database/migrations/2025_07_01_122908_create_gift_card_tables.php': 'afc2a6d3b0f3793dcce770e5b150488022471f0121558723c549755cb8169425', 'database/migrations/2025_07_13_224539_add_column_rate_time_ranges_to_v2_server_table.php': '53aefe1814403fd9255d580cfa24633524f8f21eaf3b7466c97d61bbbec2851b', 'database/migrations/2025_07_26_000001_add_type_to_plugins_table.php': 'f9914b3dd64b6b8251b283780b459c9f8541b0668591cedb345a7775b4aed07b', 'database/migrations/2025_07_27_000001_create_v2_subscribe_templates_table.php': '90b24a77e47006dcd84be9b85c286ae652a704ad8341bc6e1123bc0c4fb3e257', 'database/migrations/2026_03_11_000001_replace_v2_log_with_admin_audit_log.php': 'd3027633300cce40b7c75779a9169ae8119d5228cb649ca97ae60bdce220320f', 'database/migrations/2026_03_11_000002_add_stat_user_record_at_index.php': '87c8458bc15924f7d15de782badedb3e6abdb56afffdb902037440567333132d', 'database/migrations/2026_03_15_060035_add_custom_config_and_cert_to_v2_server_table.php': 'ba6402218cbd3f634c2bc13b8a749599185cd354a108f97bce6a96ed4222a8fe', 'database/migrations/2026_03_28_161536_add_traffic_fields_to_servers.php': 'd7e1e3f7b9e8baeec54fabcebe09cfff2ab46fe04d798cc81047a55a39ea60a1', 'database/migrations/2026_04_11_000001_add_machine_support.php': '7122acfdcc02930f8cd5d4a81af3c8aa386646be31153d35af0c749d0fd6f1f0', 'database/migrations/2026_04_18_000001_add_network_to_machine_load_history.php': 'a26930e613b7c5c2f71526a6c21922246656a0563a620c63eeb7e07016b5c3c8', 'database/migrations/2026_04_18_000001_make_server_enabled_nullable.php': '7cae1fc8d42687edd4b0562abc31afffd86e0482afcb3d5eb3e09449ea08e11d', 'database/migrations/2026_04_19_000001_backfill_users_next_reset_at.php': '4aee68e8fe809ffdd4a773866995d742b3991a4dce8a9c0ad206211536408da4', 'database/migrations/2026_04_19_235904_backfill_utls_for_legacy_servers.php': '4411e2aee051974c7c3936db5c70d0d0edd5233b25be69971a0ca5c5cb45ffe7', 'database/migrations/2026_04_20_000001_create_mail_templates_table.php': 'd93cec478ae56af162e20943a9c6d9d1c05fba3633ed759d451d10bd95ccc5b1', 'database/migrations/2026_04_21_000001_fix_ticket_reply_status.php': '750f7ec8419ddb2c1907b20d42c9f57490cac86e14d10fb935222baeea370106', 'database/migrations/2026_04_22_000001_normalize_trojan_tls_settings.php': '4d12292997576dc410aacc40ddd1ab45b01eb06d7dda8006712ebfc6d256d0ce', 'database/migrations/2026_05_30_000000_rename_refund_amount_to_surplus_credit.php': '05e796d3bf38aa214210a3f80c5ac0d87173f6eeb60a7d3a54c6e33923f73ccc', 'routes/channels.php': '30d152943410d7da9d07ffd93390435dc880b54527be410ebeae17b312404793', 'routes/console.php': 'bad973163f0cfc180e058ecc776649cfd0610988cea2dbd5ad92ab24e78d164b', 'routes/web.php': 'eb5efab755fda050b8617bc1cff07f71e6575831783ac81b935fc56fc67530ea'}
LABEL = 'com.codex.hy2-auto-panel-fixture'
OWNER = {'tool': 'Codex Xboard HY2 auto panel fixture', 'path': str(BASE), 'date': '2026-10-25'}
MOUNTS = {'.env': '/www/.env', 'storage-app': '/www/storage/app', '.dockerdata': '/www/.docker/.data', 'plugins': '/www/plugins'}
ENV = {'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
       'HOME': str(PRIVATE), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8', 'PYTHONDONTWRITEBYTECODE': '1'}


class FixtureError(Exception):
    pass


def require(value, reason):
    if not value:
        raise FixtureError(reason)


def write(path, value):
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
    path.chmod(0o600)


def read(path):
    return json.loads(path.read_text())


def root():
    require(os.geteuid() == 0 and sys.platform == 'linux', 'root-linux-required')


def owned():
    root()
    require(BASE.is_dir() and not BASE.is_symlink() and BASE.resolve() == BASE, 'fixture-directory-differs')
    require(read(BASE / 'owner.json') == OWNER, 'fixture-marker-differs')
    require(PRIVATE.is_dir() and not PRIVATE.is_symlink(), 'private-directory-differs')
    for path in [BASE, PRIVATE]:
        require(path.stat().st_uid == 0 and stat.S_IMODE(path.stat().st_mode) == 0o700, 'fixture-privacy-differs')


def run(argv, label, *, data=None, timeout=90, check=True):
    require(re.fullmatch('[a-z0-9-]+', label), 'diagnostic-label-differs')
    try:
        result = subprocess.run([str(x) for x in argv], input=data, capture_output=True,
                                cwd=PRIVATE, env=ENV, timeout=timeout)
    except subprocess.TimeoutExpired as error:
        (PRIVATE / (label + '.out')).write_bytes(error.stdout or b'')
        (PRIVATE / (label + '.err')).write_bytes(error.stderr or b'')
        raise FixtureError(label + '-timeout')
    (PRIVATE / (label + '.out')).write_bytes(result.stdout)
    (PRIVATE / (label + '.err')).write_bytes(result.stderr)
    if check:
        require(result.returncode == 0, label + '-exit-' + str(result.returncode))
    return result


def docker(args, label, **kwargs):
    return run(['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock', '--config', PRIVATE / 'docker-config', *args], label, **kwargs)


def inspect(name=NAME):
    result = docker(['container', 'inspect', name], 'inspect-' + ('main' if name == NAME else 'install'), check=False)
    if result.returncode:
        require(result.returncode == 1, 'container-inspect-failed')
        return None
    rows = json.loads(result.stdout)
    require(len(rows) == 1, 'container-inspect-count-differs')
    row = rows[0]
    require(row['Config'].get('Labels', {}).get(LABEL) == str(BASE), 'container-owner-differs')
    require(row['Image'] == IMAGE, 'container-image-differs')
    binds = {item['Destination']: item['Source'] for item in row['Mounts'] if item['Type'] == 'bind'}
    require(binds == {target: str(BASE / source) for source, target in MOUNTS.items()}, 'container-binds-differ')
    return row


def common():
    args = ['--label', LABEL + '=' + str(BASE)]
    for source, target in MOUNTS.items():
        args += ['--mount', 'type=bind,src=' + str(BASE / source) + ',dst=' + target]
    args += ['--mount', 'type=volume,src=' + VOLUME + ',dst=/data']
    for key, value in {'ENABLE_SQLITE': 'true', 'ENABLE_REDIS': 'true',
                       'ADMIN_ACCOUNT': 'hy2-auto-admin@fixture.invalid', 'RESOURCE_PROFILE': 'minimal',
                       'OCTANE_WORKERS': '1', 'OCTANE_TASK_WORKERS': '1',
                       'HORIZON_DATA_PIPELINE_MAX': '1', 'HORIZON_BUSINESS_MAX': '1',
                       'HORIZON_NOTIFICATION_MAX': '1'}.items():
        args += ['--env', key + '=' + value]
    return args


def prepare():
    root()
    if BASE.exists():
        owned()
        require((BASE / 'state.json').is_file(), 'fixture-state-missing')
        print(json.dumps({'result': 'PASS', 'phase': 'prepared', 'reused': True, 'port': PORT}))
        return
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', PORT))
    BASE.mkdir(mode=0o700)
    PRIVATE.mkdir(mode=0o700)
    (PRIVATE / 'docker-config').mkdir(mode=0o700)
    write(BASE / 'owner.json', OWNER)
    write(BASE / 'state.json', {'phase': 'preparing'})
    require(inspect() is None and inspect(NAME + '-install') is None, 'container-name-in-use')
    image = docker(['image', 'inspect', IMAGE, '--format', '{{.Id}}'], 'image-check')
    require(image.stdout.decode().strip() == IMAGE, 'required-image-missing')
    existing = docker(['volume', 'inspect', VOLUME], 'volume-check', check=False)
    require(existing.returncode == 1, 'volume-name-in-use')
    docker(['volume', 'create', '--label', LABEL + '=' + str(BASE), VOLUME], 'volume-create')
    (BASE / '.env').touch(mode=0o600)
    for name in ['storage-app', '.dockerdata', 'plugins']:
        path = BASE / name
        path.mkdir(mode=0o755)
        os.chown(path, 1000, 1000)
    build_fixed_image()
    write(BASE / 'state.json', {'phase': 'prepared', 'image': IMAGE})
    print(json.dumps({'result': 'PASS', 'phase': 'prepared', 'reused': False, 'port': PORT}))


def install():
    owned()
    state = read(BASE / 'state.json')
    if state['phase'] in ['installed', 'running']:
        print(json.dumps({'result': 'PASS', 'phase': 'installed', 'reused': True}))
        return
    require(state['phase'] == 'prepared', 'install-state-differs')
    require((BASE / '.env').stat().st_size == 0, 'env-not-empty-before-install')
    require(inspect() is None and inspect(NAME + '-install') is None, 'fixture-container-already-exists')
    write(BASE / 'state.json', {'phase': 'install-started', 'image': IMAGE})
    docker(['run', '--rm', '--network=none', '--name', NAME + '-install', *common(), IMAGE,
            'php', 'artisan', 'xboard:install', '--database=sqlite', '--no-interaction'], 'install', timeout=240)
    require((BASE / '.dockerdata/database.sqlite').is_file(), 'installed-database-missing')
    require(re.search(r'^INSTALLED=(true|1)$', (BASE / '.env').read_text(), re.M), 'installed-env-marker-missing')
    write(BASE / 'state.json', {'phase': 'installed', 'image': IMAGE})
    print(json.dumps({'result': 'PASS', 'phase': 'installed', 'credential_output': 'root-private-only'}))


def start():
    owned()
    require(read(BASE / 'state.json')['phase'] in ['installed', 'running'], 'start-state-differs')
    row = inspect()
    if row:
        require(row['State']['Running'], 'owned-container-not-running')
    else:
        with socket.socket() as listener:
            listener.bind(('127.0.0.1', PORT))
        docker(['run', '-d', '--name', NAME, *common(), '--publish', '127.0.0.1:' + str(PORT) + ':7001', IMAGE], 'start', timeout=120)
    write(BASE / 'state.json', {'phase': 'running', 'image': IMAGE})
    verify()


def verify():
    owned()
    row = inspect()
    require(row and row['State']['Running'], 'fixture-not-running')
    require(row['HostConfig'].get('PortBindings') == {'7001/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(PORT)}]}, 'port-binding-differs')
    http_status = None
    for _ in range(45):
        try:
            connection = http.client.HTTPConnection('127.0.0.1', PORT, timeout=2)
            connection.request('GET', '/')
            response = connection.getresponse()
            http_status = response.status
            response.read(1048576)
            connection.close()
            if http_status == 200:
                break
        except OSError:
            pass
        time.sleep(1)
    require(http_status == 200, 'fixture-http-not-200')
    db_path = BASE / '.dockerdata/database.sqlite'
    require(db_path.is_file() and not db_path.is_symlink(), 'database-path-differs')
    with sqlite3.connect('file:' + str(db_path) + '?mode=ro', uri=True) as db:
        rows = db.execute("SELECT code, version, is_enabled FROM v2_plugins WHERE code IN ('node_endpoint','smart_expiry','qrcodeextend','flowscope') ORDER BY code").fetchall()
    expected = {'node_endpoint': '1.0.7', 'smart_expiry': '1.2.0', 'qrcodeextend': '1.2.0', 'flowscope': '2.4.0'}
    require({code: version for code, version, enabled in rows} == expected and all(enabled for code, version, enabled in rows), 'bundled-plugins-differ')
    report = {'result': 'PASS', 'phase': 'verified', 'container': NAME, 'container_id': row['Id'],
              'image': row['Image'], 'http_status': http_status, 'port': PORT,
              'plugins': [{'code': code, 'version': version, 'enabled': bool(enabled)} for code, version, enabled in rows],
              'database': 'isolated-sqlite', 'host_nginx_changed': False}
    write(BASE / 'verification.json', report)
    print(json.dumps(report))


def php_load():
    owned()
    row = inspect()
    require(row and row['State']['Running'], 'fixture-not-running')
    data = sys.stdin.buffer.read(1048577)
    require(0 < len(data) <= 1048576 and data.lstrip().startswith(b'<?php'), 'php-seed-input-differs')
    digest = hashlib.sha256(data).hexdigest()
    (PRIVATE / 'seed-program.php').write_bytes(data)
    code = "$p='/tmp/codex-hy2-auto-panel-seed-20261025.php'; $d=stream_get_contents(STDIN); if(file_put_contents($p,$d)!==strlen($d)){exit(1);} chmod($p,0600);"
    docker(['exec', '--user', '1000:1000', '--workdir', '/www', '-i', NAME, 'php', '-r', code], 'seed-program-load', data=data)
    write(PRIVATE / 'seed-program.json', {'sha256': digest})
    print(json.dumps({'result': 'PASS', 'phase': 'php-load', 'input_sha256': digest}))


def seed():
    owned()
    row = inspect()
    require(row and row['State']['Running'], 'fixture-not-running')
    require((PRIVATE / 'seed-program.json').is_file(), 'seed-program-not-loaded')
    digest = read(PRIVATE / 'seed-program.json')['sha256']
    action = sys.argv[2] if len(sys.argv) == 3 else 'seed'
    require(action in ['seed', 'get-safe-config', 'mutate-range', 'disable'], 'seed-action-differs')
    data = sys.stdin.buffer.read(1048577)
    require(len(data) <= 1048576, 'seed-input-too-large')
    if data.strip():
        require(isinstance(json.loads(data), dict), 'seed-input-must-be-object')
    label = 'seed-' + action + '-' + str(time.time_ns())
    result = docker(['exec', '--user', '1000:1000', '--workdir', '/www',
                     '--env', 'XBOARD_HY2_AUTO_PANEL_FIXTURE=20261025', '-i', NAME, 'php',
                     '/tmp/codex-hy2-auto-panel-seed-20261025.php', action], label, data=data, timeout=120)
    write(PRIVATE / 'last-seed.json', {'label': label, 'input_sha256': digest, 'exit': result.returncode})
    print(json.dumps({'result': 'PASS', 'phase': 'seed', 'input_sha256': digest, 'exit': 0,
                      'action': action, 'output_path': str(PRIVATE / (label + '.out')), 'output_bytes': len(result.stdout)}))


def cleanup():
    owned()
    require(inspect(NAME + '-install') is None, 'install-container-still-present')
    row = inspect()
    if row:
        docker(['container', 'rm', '-f', NAME], 'cleanup-container', timeout=90)
    require(inspect() is None, 'cleanup-container-remains')
    state = read(BASE / 'state.json')
    if state['phase'] == 'running':
        state['phase'] = 'installed'
    write(BASE / 'state.json', state)
    print(json.dumps({'result': 'PASS', 'phase': 'cleanup', 'container_removed': bool(row), 'data_retained': True}))



def build_fixed_image():
    global IMAGE
    archive = Path('/tmp/codex-hy2-auto-panel-overlay-20261025.tar.gz')
    require(archive.is_file() and not archive.is_symlink(), 'fix-archive-missing')
    require(hashlib.sha256(archive.read_bytes()).hexdigest() == ARCHIVE_SHA, 'fix-archive-hash-differs')
    context = PRIVATE / 'image-context'
    require(not context.exists(), 'image-context-already-exists')
    context.mkdir(mode=0o700)
    allowed = set(FIX_FILES) | {'Dockerfile', 'manifest.json'}
    with tarfile.open(archive, 'r:gz') as package:
        members = package.getmembers()
        require(len(members) == len(allowed) and {item.name for item in members} == allowed, 'fix-archive-members-differ')
        for item in members:
            require(item.isfile() and not item.issym() and not item.islnk(), 'fix-archive-type-differs')
            destination = context / item.name
            require(destination.resolve().is_relative_to(context), 'fix-archive-path-differs')
            data = package.extractfile(item).read()
            if item.name in FIX_FILES:
                require(hashlib.sha256(data).hexdigest() == FIX_FILES[item.name], 'fix-source-hash-differs')
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            destination.write_bytes(data)
            destination.chmod(0o600)
    require(read(context / 'manifest.json') == FIX_FILES, 'fix-manifest-differs')
    base_tag = 'codex-xboard-hy2-auto-base:20261025'
    current = docker(['image', 'inspect', base_tag], 'base-tag-inspect', check=False)
    if current.returncode == 0:
        require(json.loads(current.stdout)[0]['Id'] == IMAGE, 'base-tag-already-differs')
    else:
        require(current.returncode == 1, 'base-tag-inspect-failed')
        docker(['image', 'tag', IMAGE, base_tag], 'base-tag-create')
    result = docker(['build', '--pull=false', '--network=none', '--tag',
        'codex-xboard-hy2-auto-panel:20261025', str(context)], 'image-build', timeout=180)
    built = docker(['image', 'inspect', 'codex-xboard-hy2-auto-panel:20261025'], 'fixed-image-inspect')
    rows = json.loads(built.stdout)
    require(len(rows) == 1 and rows[0]['Config'].get('Labels', {}).get('com.codex.hy2-auto-panel-image') == '20261025',
        'fixed-image-label-differs')
    IMAGE = rows[0]['Id']
    require(re.fullmatch('sha256:[0-9a-f]{64}', IMAGE), 'fixed-image-id-differs')
    write(BASE / 'source-fix.json', {'archive_sha256': ARCHIVE_SHA, 'files': FIX_FILES, 'image': IMAGE})


def credentials():
    owned()
    require(inspect() and (PRIVATE / 'seed-program.json').is_file(), 'fixture-seed-not-ready')
    destination = PRIVATE / 'credentials.private.json'
    require(not destination.is_symlink(), 'credentials-destination-is-symlink')
    args = ['/usr/bin/docker', '--host', 'unix:///var/run/docker.sock', '--config', str(PRIVATE / 'docker-config'),
        'exec', '--user', '1000:1000', '--workdir', '/www',
        '--env', 'XBOARD_HY2_AUTO_PANEL_FIXTURE=20261025', NAME, 'php',
        '/tmp/codex-hy2-auto-panel-seed-20261025.php', 'private-credentials']
    result = subprocess.run(args, capture_output=True, cwd=PRIVATE, env=ENV, timeout=60)
    require(result.returncode == 0, 'private-credentials-export-failed')
    value = json.loads(result.stdout)
    require(value.get('private') is True, 'credentials-private-marker-differs')
    write(destination, value)
    print(json.dumps({'result': 'PASS', 'phase': 'credentials', 'output': 'root-private-only'}))


def safe_config():
    owned()
    row = inspect()
    require(row and row['State']['Running'], 'fixture-not-running')
    response = docker(['exec', '--user', '1000:1000', '--workdir', '/www',
        '--env', 'XBOARD_HY2_AUTO_PANEL_FIXTURE=20261025', NAME, 'php',
        '/tmp/codex-hy2-auto-panel-seed-20261025.php', 'get-safe-config'], 'safe-config', timeout=60)
    value = json.loads(response.stdout)
    allowed = {'node_id', 'protocol', 'version', 'advertised_port', 'server_port',
               'port_hopping', 'hop_interval', 'enabled', 'ws_node_alive',
               'ws_worker_heartbeat', 'pull_interval'}
    require(set(value) == allowed, 'safe-config-public-fields-differ')
    require(value['protocol'] == 'hysteria' and value['version'] == 2, 'safe-config-protocol-differs')
    write(BASE / 'safe-config.json', value)
    print(json.dumps({'result': 'PASS', 'phase': 'safe-config', **value}))


if __name__ == '__main__':
    try:
        require(2 <= len(sys.argv) <= 3, 'one-phase-required')
        phase = sys.argv[1]
        require(phase in ['prepare', 'install', 'start', 'verify', 'php-load', 'seed', 'cleanup', 'credentials', 'safe-config'], 'phase-differs')
        require(len(sys.argv) == 2 or phase == 'seed', 'extra-argument-differs')
        if phase != 'prepare':
            owned()
            IMAGE = read(BASE / 'state.json')['image']
            require(re.fullmatch('sha256:[0-9a-f]{64}', IMAGE), 'state-image-differs')
        globals()[phase.replace('-', '_')]()
    except FixtureError as error:
        print(json.dumps({'result': 'FAIL', 'reason': str(error)}))
        sys.exit(1)
    except Exception as error:
        print(json.dumps({'result': 'FAIL', 'reason': 'fixture-operation-failed', 'type': type(error).__name__}))
        sys.exit(1)
