package __PACOTE__;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(RastreioPlugin.class); // GPS em segundo plano (só no app do motorista)
        super.onCreate(savedInstanceState);
    }
}
